"""Tests for the central usage ledger and its source breakdown API (Phase 10).

Covers recording/normalisation/scoping (issues #429, #432), aggregation by
source including unknown historical values (#451), and the instrumentation of
project chat and project analysis.
"""

import json

import pytest

from app.extensions import db
from app.models import Conversation, Message, Project, ProjectFile, UsageRecord, Workspace
from app.models.project import SOURCE_ARCHIVE, STATUS_READY
from app.services import usage_ledger


def _project(make_user, login, username="ledgeruser", email="ledgeruser@example.com"):
    user = make_user(username=username, email=email)
    login(email=email)
    workspace = Workspace(user_id=user.id, name="Ledger workspace")
    db.session.add(workspace)
    db.session.commit()
    project = Project(
        workspace_id=workspace.id,
        user_id=user.id,
        name="Ledger project",
        source=SOURCE_ARCHIVE,
        status=STATUS_READY,
    )
    db.session.add(project)
    db.session.commit()
    db.session.add(
        ProjectFile(
            project_id=project.id,
            path="app.py",
            size=12,
            is_binary=False,
            content="def main(): pass\n",
        )
    )
    db.session.commit()
    return user, workspace, project


class TestRecordUsage:
    def test_records_counts_scope_and_cost(self, db, make_user):
        user = make_user(username="ledger", email="ledger@example.com")
        workspace = Workspace(user_id=user.id, name="W")
        db.session.add(workspace)
        db.session.commit()
        project = Project(
            workspace_id=workspace.id,
            user_id=user.id,
            name="P",
            source=SOURCE_ARCHIVE,
            status=STATUS_READY,
        )
        db.session.add(project)
        db.session.commit()

        record = usage_ledger.record_usage(
            source=" Analysis ",
            prompt_tokens=100,
            completion_tokens=50,
            user=user,
            project=project,
            provider="openai",
            model="gpt-4o",
            tokens_estimated=True,
        )

        assert record.id is not None
        assert record.source == "analysis"
        assert record.workspace_id == workspace.id
        assert record.project_id == project.id
        assert record.user_id == user.id
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
        assert record.total_tokens == 150
        assert record.tokens_estimated is True
        assert record.cost_estimate == pytest.approx(0.00075)

    def test_missing_counts_default_to_zero(self, db):
        record = usage_ledger.record_usage(source="tools", provider="mock", model="mock-1")
        assert record.prompt_tokens == 0
        assert record.completion_tokens == 0
        assert record.total_tokens == 0
        assert record.cost_estimate == 0.0

    def test_unknown_model_cost_stays_unknown(self, db):
        record = usage_ledger.record_usage(
            source="chat",
            prompt_tokens=10,
            provider="openai",
            model="not-a-real-model",
        )
        assert record.cost_estimate is None

    def test_negative_counts_are_rejected(self, db):
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", prompt_tokens=-1)
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", completion_tokens=-1)
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", cost_estimate=-0.1)

    def test_records_request_latency_and_status(self, db):
        record = usage_ledger.record_usage(
            source="review",
            provider="openai",
            model="gpt-4o",
            request_id="  req-123  ",
            latency_ms=1250,
            status="SUCCESS",
        )

        assert record.request_id == "req-123"
        assert record.latency_ms == 1250
        assert record.status == "success"
        payload = record.to_dict()
        assert payload["request_id"] == "req-123"
        assert payload["latency_ms"] == 1250
        assert payload["status"] == "success"

    def test_correlation_fields_default_to_none(self, db):
        record = usage_ledger.record_usage(source="tools", provider="mock", model="mock-1")

        assert record.request_id is None
        assert record.latency_ms is None
        assert record.status is None
        assert record.to_dict()["request_id"] is None

    def test_request_id_is_bounded(self, db):
        record = usage_ledger.record_usage(source="chat", request_id="x" * 200)

        assert record.request_id is not None
        assert len(record.request_id) == 64

    def test_invalid_latency_is_rejected(self, db):
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", latency_ms=-1)
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", latency_ms="not-a-number")


class TestBreakdown:
    def test_groups_known_and_unknown_sources(self, db, make_user):
        owner = make_user(username="owner", email="owner@example.com")
        other = make_user(username="other", email="other@example.com")

        rows = [
            ("chat", 10, 5),
            ("chat", 1, 1),
            ("analysis", 20, 10),
            ("project_chat", 3, 2),
            ("review", 0, 4),
            ("tools", 5, 0),
            ("legacy-source", 7, 7),
        ]
        for source, prompt, completion in rows:
            usage_ledger.record_usage(
                source=source,
                prompt_tokens=prompt,
                completion_tokens=completion,
                user=owner,
                provider="mock",
                model="mock-1",
            )
        usage_ledger.record_usage(
            source="chat",
            prompt_tokens=999,
            completion_tokens=999,
            user=other,
            provider="mock",
            model="mock-1",
        )

        breakdown = usage_ledger.breakdown_by_source(user_id=owner.id)

        assert breakdown["chat"]["count"] == 2
        assert breakdown["chat"]["prompt_tokens"] == 11
        assert breakdown["chat"]["total_tokens"] == 17
        assert breakdown["analysis"]["count"] == 1
        assert breakdown["analysis"]["total_tokens"] == 30
        assert breakdown["review"]["count"] == 1
        assert breakdown["tools"]["count"] == 1
        assert breakdown["project_chat"]["count"] == 1
        assert breakdown["unknown"]["count"] == 1
        assert breakdown["unknown"]["total_tokens"] == 14
        assert breakdown["chat"]["cost_estimate"] == 0.0
        assert sum(item["count"] for item in breakdown.values()) == 7

    def test_scope_filters_are_applied(self, db, make_user):
        user = make_user(username="scope", email="scope@example.com")
        workspace = Workspace(user_id=user.id, name="Scoped")
        db.session.add(workspace)
        db.session.commit()
        project = Project(
            workspace_id=workspace.id,
            user_id=user.id,
            name="Scoped project",
            source=SOURCE_ARCHIVE,
            status=STATUS_READY,
        )
        db.session.add(project)
        db.session.commit()

        usage_ledger.record_usage(source="chat", user=user, prompt_tokens=5)
        usage_ledger.record_usage(source="analysis", user=user, project=project, prompt_tokens=7)

        by_workspace = usage_ledger.breakdown_by_source(user_id=user.id, workspace_id=workspace.id)
        by_project = usage_ledger.breakdown_by_source(user_id=user.id, project_id=project.id)

        assert sum(item["count"] for item in by_workspace.values()) == 1
        assert by_workspace["analysis"]["total_tokens"] == 7
        assert sum(item["count"] for item in by_project.values()) == 1
        assert by_project["analysis"]["total_tokens"] == 7


class TestBreakdownApi:
    def test_requires_authentication(self, client):
        response = client.get("/api/usage/breakdown")
        assert response.status_code == 401
        assert response.mimetype == "application/problem+json"

    def test_returns_counts_totals_and_currency(self, client, db, make_user, login):
        user = make_user(username="breakdown", email="breakdown@example.com")
        login(email="breakdown@example.com")
        usage_ledger.record_usage(
            source="chat", user=user, provider="mock", model="mock-1", prompt_tokens=10
        )
        usage_ledger.record_usage(
            source="analysis",
            user=user,
            provider="mock",
            model="mock-1",
            prompt_tokens=20,
            completion_tokens=5,
        )
        usage_ledger.record_usage(source="mystery", user=user, prompt_tokens=1)

        response = client.get("/api/usage/breakdown")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["currency"]["code"] == "USD"
        assert payload["cost_is_estimate"] is True
        assert payload["by_source"]["chat"]["count"] == 1
        assert payload["by_source"]["analysis"]["total_tokens"] == 25
        assert payload["by_source"]["unknown"]["count"] == 1
        assert payload["totals"]["count"] == 3
        assert payload["totals"]["total_tokens"] == 36
        assert {item["source"] for item in payload["sources"]} >= {
            "chat",
            "analysis",
            "unknown",
        }

    def test_other_users_usage_is_not_visible(self, client, db, make_user, login):
        owner = make_user(username="api-owner", email="api-owner@example.com")
        usage_ledger.record_usage(source="chat", user=owner, prompt_tokens=100)
        make_user(username="api-other", email="api-other@example.com")
        login(email="api-other@example.com")

        payload = client.get("/api/usage/breakdown").get_json()

        assert payload["totals"]["count"] == 0
        assert all(item["count"] == 0 for item in payload["sources"])


class TestProjectInstrumentation:
    def test_project_chat_records_a_project_chat_row(self, client, db, make_user, login):
        user, workspace, project = _project(make_user, login)

        response = client.post(
            f"/workspaces/api/projects/{project.id}/chat",
            json={"content": "What does app.py do?"},
        )

        assert response.status_code == 201
        rows = UsageRecord.query.filter_by(source="project_chat").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.user_id == user.id
        assert row.workspace_id == workspace.id
        assert row.project_id == project.id
        assert row.total_tokens > 0
        assert row.tokens_estimated is True
        assert row.provider == "mock"
        assert row.model == "mock-1"

    def test_project_chat_stream_records_a_row(self, client, db, make_user, login):
        _, workspace, project = _project(
            make_user, login, username="streamuser", email="streamuser@example.com"
        )

        response = client.post(
            f"/workspaces/api/projects/{project.id}/chat/stream",
            json={"content": "Explain helper.py"},
        )

        assert response.status_code == 200
        assert '"type": "done"' in response.get_data(as_text=True)
        rows = UsageRecord.query.filter_by(source="project_chat").all()
        assert len(rows) == 1
        assert rows[0].workspace_id == workspace.id
        assert rows[0].project_id == project.id
        assert rows[0].total_tokens > 0

    def test_each_project_analysis_records_a_row(self, client, db, make_user, login):
        user, workspace, project = _project(
            make_user, login, username="analysisuser", email="analysisuser@example.com"
        )

        for kind in ("bugs", "architecture"):
            response = client.post(
                f"/workspaces/api/projects/{project.id}/analyze", json={"kind": kind}
            )
            assert response.status_code == 200

        rows = UsageRecord.query.filter_by(source="analysis").order_by(UsageRecord.id).all()
        assert len(rows) == 2
        for row in rows:
            assert row.user_id == user.id
            assert row.workspace_id == workspace.id
            assert row.project_id == project.id
            assert row.total_tokens > 0
            assert row.tokens_estimated is True
            assert row.provider == "mock"
            assert row.model == "mock-1"


class TestMessageBackfill:
    def _conversation(self, make_user, **kwargs):
        user = make_user(**kwargs)
        conversation = Conversation(user_id=user.id, title="Historical chat")
        db.session.add(conversation)
        db.session.commit()
        return user, conversation

    def _message(self, conversation, **fields):
        values = {"role": "assistant", "content": "historical reply", **fields}
        message = Message(conversation_id=conversation.id, **values)
        db.session.add(message)
        db.session.commit()
        return message

    def test_empty_database_reports_zero(self, app):
        result = app.test_cli_runner().invoke(args=["usage", "backfill", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["scanned"] == 0
        assert payload["created"] == 0
        assert payload["skipped_existing"] == 0
        assert payload["batches"] == 0

    def test_backfills_chat_messages_and_is_idempotent(self, app, db, make_user):
        user, conversation = self._conversation(make_user)
        first = self._message(
            conversation,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        second = self._message(
            conversation,
            prompt_tokens=4,
            completion_tokens=6,
            total_tokens=10,
        )
        self._message(conversation, role="user", content="no usage")  # ignored

        runner = app.test_cli_runner()
        initial = runner.invoke(args=["usage", "backfill", "--json"])

        assert initial.exit_code == 0
        initial_payload = json.loads(initial.output)
        assert initial_payload["scanned"] == 2
        assert initial_payload["created"] == 2
        assert initial_payload["skipped_existing"] == 0

        rows = UsageRecord.query.filter_by(source="chat").order_by(UsageRecord.id).all()
        assert len(rows) == 2
        assert {(row.prompt_tokens, row.completion_tokens, row.total_tokens) for row in rows} == {
            (10, 5, 15),
            (4, 6, 10),
        }
        assert all(row.user_id == user.id for row in rows)
        assert all(row.conversation_id == conversation.id for row in rows)
        assert all(row.tokens_estimated is True for row in rows)

        # The source message rows are never modified.
        assert db.session.get(Message, first.id).prompt_tokens == 10
        assert db.session.get(Message, second.id).prompt_tokens == 4

        repeat = runner.invoke(args=["usage", "backfill", "--json"])
        assert repeat.exit_code == 0
        repeat_payload = json.loads(repeat.output)
        assert repeat_payload["scanned"] == 2
        assert repeat_payload["created"] == 0
        assert repeat_payload["skipped_existing"] == 2
        assert UsageRecord.query.filter_by(source="chat").count() == 2

    def test_batch_size_and_limit_bound_the_run(self, app, db, make_user):
        _, conversation = self._conversation(make_user)
        for _ in range(3):
            self._message(
                conversation,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )

        result = app.test_cli_runner().invoke(
            args=["usage", "backfill", "--batch-size", "1", "--limit", "2", "--json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["scanned"] == 2
        assert payload["created"] == 2
        assert payload["batches"] == 2
        assert UsageRecord.query.filter_by(source="chat").count() == 2

    def test_dry_run_writes_nothing(self, app, db, make_user):
        _, conversation = self._conversation(make_user)
        self._message(
            conversation,
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
        )

        result = app.test_cli_runner().invoke(args=["usage", "backfill", "--dry-run", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["created"] == 1
        assert UsageRecord.query.filter_by(source="chat").count() == 0


class TestReviewInstrumentation:
    def test_each_review_records_a_scoped_review_row(self, client, db, make_user, login):
        user, workspace, project = _project(
            make_user,
            login,
            username="reviewuser",
            email="reviewuser@example.com",
        )

        for kind in ("quality", "security"):
            response = client.post(
                "/reviews/api/reviews",
                json={"source": "project", "project_id": project.id, "kind": kind},
            )
            assert response.status_code == 201

        rows = UsageRecord.query.filter_by(source="review").order_by(UsageRecord.id).all()
        assert len(rows) == 2
        for row in rows:
            assert row.user_id == user.id
            assert row.workspace_id == workspace.id
            assert row.project_id == project.id
            assert row.total_tokens > 0
            assert row.tokens_estimated is True
            assert row.provider == "mock"
            assert row.model == "mock-1"
            assert row.status == usage_ledger.STATUS_SUCCESS
            assert row.request_id
            assert row.latency_ms is not None
            assert row.latency_ms >= 0

    def test_failed_review_records_an_error_row(self, client, db, make_user, login, monkeypatch):
        from app.services.llm import LLMProviderError

        _, workspace, project = _project(
            make_user,
            login,
            username="failedreview",
            email="failedreview@example.com",
        )

        def boom():
            raise LLMProviderError("provider unavailable")

        monkeypatch.setattr("app.services.reviews.get_provider", boom)
        response = client.post(
            "/reviews/api/reviews",
            json={"source": "project", "project_id": project.id, "kind": "quality"},
        )

        assert response.status_code == 201
        assert response.get_json()["status"] == "failed"
        rows = UsageRecord.query.filter_by(source="review").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.workspace_id == workspace.id
        assert row.project_id == project.id
        assert row.total_tokens == 0
        assert row.tokens_estimated is True
        assert row.status == usage_ledger.STATUS_ERROR
        assert row.request_id
        assert row.latency_ms is not None


class TestNormalizationAndValidation:
    def test_source_labels_are_normalized_and_bucketed(self):
        assert usage_ledger.normalize_source("  REVIEW ") == "review"
        assert usage_ledger.normalize_source(None) is None
        assert usage_ledger.normalize_source("") is None
        assert usage_ledger.source_bucket(" ANALYSIS ") == "analysis"
        assert usage_ledger.source_bucket("legacy-source") == usage_ledger.UNKNOWN_SOURCE
        assert usage_ledger.source_bucket(None) == usage_ledger.UNKNOWN_SOURCE
        assert len(usage_ledger.normalize_source("x" * 100)) == 40

    def test_normalize_usage_derives_total_and_validates_explicit_values(self):
        assert usage_ledger.normalize_usage(3, 4) == {
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "total_tokens": 7,
        }
        assert usage_ledger.normalize_usage(None, "", total_tokens=None)["total_tokens"] == 0
        assert usage_ledger.normalize_usage(0, 0, total_tokens=0)["total_tokens"] == 0
        with pytest.raises(ValueError):
            usage_ledger.normalize_usage("not-a-number")
        with pytest.raises(ValueError):
            usage_ledger.normalize_usage(total_tokens=-1)

    def test_record_usage_scope_objects_win_and_optional_fields_are_bounded(self, db, make_user):
        user = make_user(username="scoped", email="scoped@example.com")
        other = make_user(username="other", email="other@example.com")
        workspace = Workspace(user_id=user.id, name="Scoped")
        db.session.add(workspace)
        db.session.commit()
        project = Project(
            workspace_id=workspace.id,
            user_id=user.id,
            name="P",
            source=SOURCE_ARCHIVE,
            status=STATUS_READY,
        )
        conversation = Conversation(user_id=user.id, title="C")
        db.session.add_all([project, conversation])
        db.session.commit()

        record = usage_ledger.record_usage(
            source="chat",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=99,
            user=user,
            user_id=other.id,
            workspace=workspace,
            workspace_id=999,
            project=project,
            project_id=999,
            conversation=conversation,
            conversation_id=999,
            provider="  OpenAI  ",
            model="  gpt-4o  ",
            request_id="  ",
            status="ERROR",
            tokens_estimated=False,
            commit=False,
        )

        assert record.user_id == user.id
        assert record.workspace_id == workspace.id
        assert record.project_id == project.id
        assert record.conversation_id == conversation.id
        assert record.provider == "OpenAI"
        assert record.model == "gpt-4o"
        assert record.total_tokens == 99
        assert record.request_id is None
        assert record.status == "error"
        assert record.tokens_estimated is False

    def test_explicit_cost_is_validated(self, db):
        record = usage_ledger.record_usage(source="chat", cost_estimate="1.25")
        assert record.cost_estimate == pytest.approx(1.25)
        with pytest.raises(ValueError):
            usage_ledger.record_usage(source="chat", cost_estimate="not-a-number")

    def test_estimate_cost_requires_provider_and_model(self, db):
        assert usage_ledger.estimate_cost(None, "gpt-4o", 1, 1) is None
        assert usage_ledger.estimate_cost("openai", None, 1, 1) is None
        assert usage_ledger.estimate_cost("openai", "unknown-model", 1, 1) is None

    def test_current_provider_metadata_falls_back_safely(self, app, monkeypatch):
        assert usage_ledger.current_provider_metadata() == ("mock", "mock-1")

        def boom(**_kwargs):
            raise RuntimeError("configuration unavailable")

        monkeypatch.setattr("app.services.providers.get_provider", boom)
        assert usage_ledger.current_provider_metadata() == (None, None)


class TestAggregationAndListing:
    def test_query_records_filters_and_orders(self, db, make_user):
        user = make_user(username="listing", email="listing@example.com")
        other = make_user(username="listing-other", email="listing-other@example.com")
        workspace = Workspace(user_id=user.id, name="Listing")
        db.session.add(workspace)
        db.session.commit()
        project = Project(
            workspace_id=workspace.id,
            user_id=user.id,
            name="P",
            source=SOURCE_ARCHIVE,
            status=STATUS_READY,
        )
        db.session.add(project)
        db.session.commit()

        first = usage_ledger.record_usage(
            source="chat", user=user, workspace=workspace, prompt_tokens=1
        )
        second = usage_ledger.record_usage(
            source="analysis", user=user, workspace=workspace, project=project, prompt_tokens=2
        )
        third = usage_ledger.record_usage(source="chat", user=other, prompt_tokens=3)

        assert [row.id for row in usage_ledger.query_records(user_id=user.id)] == [
            first.id,
            second.id,
        ]
        assert [row.id for row in usage_ledger.query_records(workspace_id=workspace.id)] == [
            first.id,
            second.id,
        ]
        assert [row.id for row in usage_ledger.query_records(project_id=project.id)] == [second.id]
        assert [row.id for row in usage_ledger.query_records(source="analysis")] == [second.id]
        assert [row.id for row in usage_ledger.query_records(user_id=user.id, source=" CHAT ")] == [
            first.id
        ]
        assert [row.id for row in usage_ledger.query_records(source="chat")] == [first.id, third.id]

    def test_summarize_records_handles_missing_and_unknown_costs(self):
        class Row:
            def __init__(self, **fields):
                self.__dict__.update(fields)

        rows = [
            Row(
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                tokens_estimated=True,
                cost_estimate=0.5,
            ),
            Row(
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                tokens_estimated=False,
                cost_estimate=None,
            ),
            Row(
                prompt_tokens=4,
                completion_tokens=5,
                total_tokens=9,
                tokens_estimated=False,
                cost_estimate=1.25,
            ),
        ]

        summary = usage_ledger.summarize_records(rows)

        assert summary["count"] == 3
        assert summary["prompt_tokens"] == 5
        assert summary["completion_tokens"] == 7
        assert summary["total_tokens"] == 12
        assert summary["priced_records"] == 2
        assert summary["estimated_records"] == 1
        assert summary["cost_estimate"] == pytest.approx(1.75)

    def test_breakdown_payload_zeroes_known_sources_and_includes_totals(self, db, make_user):
        user = make_user(username="payload", email="payload@example.com")
        usage_ledger.record_usage(source="chat", user=user, prompt_tokens=2, completion_tokens=1)
        usage_ledger.record_usage(source="mystery", user=user, prompt_tokens=4)

        payload = usage_ledger.breakdown_payload(user_id=user.id)

        assert payload["known_sources"] == list(usage_ledger.SOURCES)
        assert payload["totals"]["count"] == 2
        assert payload["totals"]["total_tokens"] == 7
        assert payload["by_source"]["chat"]["count"] == 1
        assert payload["by_source"]["analysis"]["count"] == 0
        assert payload["by_source"]["unknown"]["count"] == 1
        assert {item["source"] for item in payload["sources"]} >= {
            "chat",
            "analysis",
            "review",
            "tools",
            "project_chat",
            "unknown",
        }

    def test_workspace_price_override_changes_ledger_cost(self, db, make_user):
        from app.services import pricing

        user = make_user(username="ledger-price", email="ledger-price@example.com")
        workspace = Workspace(user_id=user.id, name="Ledger pricing")
        db.session.add(workspace)
        db.session.commit()
        pricing.set_workspace_price_override(
            workspace,
            "openai",
            "gpt-4o",
            input_per_1k_usd=10.0,
            output_per_1k_usd=20.0,
            user=user,
        )

        record = usage_ledger.record_usage(
            source="chat",
            user=user,
            workspace=workspace,
            provider="openai",
            model="gpt-4o",
            prompt_tokens=1_000,
            completion_tokens=1_000,
        )

        assert record.cost_estimate == pytest.approx(30.0)


class TestBackfillEdgeCases:
    def _conversation(self, make_user, user=None):
        user = user or make_user(username="edge", email="edge@example.com")
        conversation = Conversation(user_id=user.id, title="Edge")
        db.session.add(conversation)
        db.session.commit()
        return user, conversation

    def test_identical_timestamps_are_not_deduplicated(self, app, db, make_user):
        from datetime import UTC, datetime

        _, conversation = self._conversation(make_user)
        stamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        messages = []
        for _ in range(2):
            message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="same",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                created_at=stamp,
            )
            db.session.add(message)
            messages.append(message)
        db.session.commit()

        result = usage_ledger.backfill_from_messages()

        assert result["created"] == 2
        rows = UsageRecord.query.filter_by(source="chat").order_by(UsageRecord.id).all()
        assert [row.request_id for row in rows] == [
            f"{usage_ledger.BACKFILL_REQUEST_PREFIX}{messages[0].id}",
            f"{usage_ledger.BACKFILL_REQUEST_PREFIX}{messages[1].id}",
        ]

        repeat = usage_ledger.backfill_from_messages()
        assert repeat["created"] == 0
        assert repeat["skipped_existing"] == 2

    def test_invalid_batch_size_and_limit_are_rejected(self, db):
        with pytest.raises(ValueError):
            usage_ledger.backfill_from_messages(batch_size=0)
        with pytest.raises(ValueError):
            usage_ledger.backfill_from_messages(batch_size=10_000)
        with pytest.raises(ValueError):
            usage_ledger.backfill_from_messages(limit=-1)

    def test_zero_limit_scans_nothing(self, db, make_user):
        _, conversation = self._conversation(make_user)
        db.session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="x",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )
        )
        db.session.commit()

        result = usage_ledger.backfill_from_messages(limit=0)

        assert result["scanned"] == 0
        assert result["created"] == 0
        assert result["batches"] == 0

    def test_incomplete_and_orphaned_messages_are_skipped(self, db, make_user):
        _, conversation = self._conversation(make_user)
        db.session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="negative",
                prompt_tokens=-1,
                completion_tokens=1,
                total_tokens=0,
            )
        )
        db.session.add(
            Message(
                conversation_id=999_999,
                role="assistant",
                content="orphan",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )
        )
        db.session.commit()

        result = usage_ledger.backfill_from_messages()

        assert result["scanned"] == 2
        assert result["created"] == 0
        assert result["skipped_incomplete"] == 1
        assert result["skipped_orphan"] == 1
