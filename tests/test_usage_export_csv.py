"""Tests for the bounded CSV usage export (#454)."""

from __future__ import annotations

import csv
import io

from app.extensions import db
from app.models import Project, Workspace
from app.models.project import SOURCE_ARCHIVE, STATUS_READY
from app.services import usage_ledger


def _setup(make_user, login):
    user = make_user(username="csv-user", email="csv@example.com")
    other = make_user(username="csv-other", email="csv-other@example.com")
    login(email="csv@example.com")
    workspace = Workspace(user_id=user.id, name="CSV workspace")
    db.session.add(workspace)
    db.session.commit()
    project = Project(
        workspace_id=workspace.id,
        user_id=user.id,
        name="CSV project",
        source=SOURCE_ARCHIVE,
        status=STATUS_READY,
    )
    db.session.add(project)
    db.session.commit()
    return user, other, workspace, project


def _record(user, **kwargs):
    return usage_ledger.record_usage(
        source=kwargs.pop("source", "chat"),
        user=user,
        provider=kwargs.pop("provider", "mock"),
        model=kwargs.pop("model", "mock-1"),
        **kwargs,
    )


def _rows(response):
    return list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))


class TestUsageExportCsv:
    def test_requires_authentication(self, client):
        response = client.get("/api/usage/export.csv")
        assert response.status_code == 401
        assert response.mimetype == "application/problem+json"

    def test_exports_header_and_only_the_current_user(self, client, db, make_user, login):
        user, other, _, _ = _setup(make_user, login)
        _record(user, prompt_tokens=10, completion_tokens=5, cost_estimate=0.25)
        _record(user, source="analysis", prompt_tokens=2, cost_estimate=None)
        _record(other, prompt_tokens=999)

        response = client.get("/api/usage/export.csv")
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert 'filename="usage.csv"' in response.headers["Content-Disposition"]
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Export-Rows"] == "2"
        assert "X-Export-Truncated" not in response.headers

        rows = _rows(response)
        assert len(rows) == 2
        assert list(rows[0]) == list(usage_ledger.EXPORT_CSV_COLUMNS)
        assert all(row["user_id"] == str(user.id) for row in rows)
        assert rows[0]["currency"] == "USD"
        assert rows[0]["provider"] == "mock"
        assert rows[0]["prompt_tokens"] == "10"
        assert rows[0]["completion_tokens"] == "5"
        assert rows[0]["total_tokens"] == "15"
        assert rows[0]["cost_estimate"] == "0.25"

    def test_escapes_commas_quotes_and_newlines(self, client, db, make_user, login):
        user, _, _, _ = _setup(make_user, login)
        provider = 'acme, "quoted"\nline'
        model = "model\twith\ttabs"
        _record(user, provider=provider, model=model, prompt_tokens=1)

        response = client.get("/api/usage/export.csv")
        rows = _rows(response)
        assert len(rows) == 1
        assert rows[0]["provider"] == provider
        assert rows[0]["model"] == model

    def test_row_cap_and_truncation_header(self, app, client, db, make_user, login):
        user, _, _, _ = _setup(make_user, login)
        app.config["USAGE_EXPORT_CSV_MAX_ROWS"] = 2
        for index in range(4):
            _record(user, prompt_tokens=index + 1)

        response = client.get("/api/usage/export.csv")
        assert response.status_code == 200
        assert response.headers["X-Export-Truncated"] == "true"
        assert response.headers["X-Export-Rows"] == "2"
        assert len(_rows(response)) == 2

    def test_configured_cap_cannot_exceed_the_hard_limit(self, app, client, db, make_user, login):
        user, _, _, _ = _setup(make_user, login)
        app.config["USAGE_EXPORT_CSV_MAX_ROWS"] = 10**9
        _record(user, prompt_tokens=1)

        response = client.get("/api/usage/export.csv")
        assert response.headers["X-Export-Rows"] == "1"

    def test_filters_only_narrow_the_user_scope(self, client, db, make_user, login):
        user, other, workspace, project = _setup(make_user, login)
        _record(user, workspace=workspace, project=project, prompt_tokens=1)
        _record(user, source="review", prompt_tokens=2)
        _record(other, workspace=workspace, prompt_tokens=50)

        workspace_rows = _rows(client.get(f"/api/usage/export.csv?workspace_id={workspace.id}"))
        assert len(workspace_rows) == 1
        assert workspace_rows[0]["workspace_id"] == str(workspace.id)

        source_rows = _rows(client.get("/api/usage/export.csv?source=%20REVIEW%20"))
        assert len(source_rows) == 1
        assert source_rows[0]["source"] == "review"

    def test_service_respects_an_explicit_small_cap(self, db, make_user):
        user = make_user(username="service-user", email="service@example.com")
        for index in range(3):
            _record(user, prompt_tokens=index + 1)

        chunks, truncated, total = usage_ledger.export_csv_chunks(user_id=user.id, max_rows=1)
        assert truncated is True
        assert total == 3
        assert len("".join(chunks).strip().splitlines()) == 2  # header + one row
