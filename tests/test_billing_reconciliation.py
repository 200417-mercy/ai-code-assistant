"""Tests for provider billing reconciliation import (Phase 10).

Fixture CSVs live in ``tests/fixtures/billing`` and are used unmodified so the
import path is exercised exactly as an operator would use it.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from app.extensions import db
from app.models import Conversation, Message, ProviderBillingRecord, User
from app.services import billing_reconciliation as br
from app.services.billing_reconciliation import BillingImportError

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "billing"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make_user(username="biller", email="biller@example.com"):
    user = User(username=username, email=email)
    user.set_password("supersecret123")
    db.session.add(user)
    db.session.commit()
    return user


def _add_usage(user, *, provider, period, tokens, role="assistant"):
    """Create a conversation with one assistant message of ``tokens`` usage."""
    year, month = (int(part) for part in period.split("-"))
    created = datetime(year, month, 15, 12, 0, 0, tzinfo=UTC)
    conversation = Conversation(user_id=user.id, title="c", provider=provider)
    db.session.add(conversation)
    db.session.commit()
    message = Message(
        conversation_id=conversation.id,
        role=role,
        content="hi",
        prompt_tokens=tokens,
        completion_tokens=0,
        total_tokens=tokens,
    )
    message.created_at = created
    db.session.add(message)
    db.session.commit()
    return message


class TestNormalizePeriod:
    def test_accepts_year_month(self):
        assert br.normalize_period("2026-01") == "2026-01"

    def test_truncates_a_full_date(self):
        assert br.normalize_period("2026-01-31") == "2026-01"

    @pytest.mark.parametrize("value", ["2026-13", "2026-00", "2026", "abcdef", "", None])
    def test_rejects_bad_periods(self, value):
        with pytest.raises(BillingImportError):
            br.normalize_period(value)


class TestParseBillingCsv:
    def test_parses_the_fixture(self, app):
        rows = br.parse_billing_csv(_fixture("providers_2026-01.csv"))
        assert [(r.provider, r.period, r.amount_usd) for r in rows] == [
            ("openai", "2026-01", 12.50),
            ("anthropic", "2026-01", 4.00),
            ("openai", "2026-02", 3.25),
        ]

    def test_lowercases_and_strips_provider(self, app):
        rows = br.parse_billing_csv("provider,period,amount_usd\n  OpenAI ,2026-01,1\n")
        assert rows[0].provider == "openai"

    def test_rejects_a_bad_period_fixture(self, app):
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv(_fixture("bad_period.csv"))
        assert exc.value.kind == "period"

    def test_rejects_a_negative_amount_fixture(self, app):
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv(_fixture("negative_amount.csv"))
        assert exc.value.kind == "amount"

    def test_rejects_a_missing_amount_column(self, app):
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv("provider,period\nopenai,2026-01\n")
        assert exc.value.kind == "header"

    def test_rejects_empty_input(self, app):
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv("   ")
        assert exc.value.kind == "empty"

    def test_rejects_a_file_with_no_data_rows(self, app):
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv("provider,period,amount_usd\n")
        assert exc.value.kind == "empty"

    def test_enforces_the_row_limit(self, app):
        body = "provider,period,amount_usd\n" + "openai,2026-01,1\n" * 3
        with pytest.raises(BillingImportError) as exc:
            br.parse_billing_csv(body, max_rows=2)
        assert exc.value.kind == "rows"


class TestEstimate:
    def test_estimate_cost_uses_the_configured_rate(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 2.0
        assert br.estimate_cost_usd(1500) == 3.0
        assert br.estimate_cost_usd(None) == 0.0

    def test_groups_usage_by_provider_and_period(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 1.0
        user = _make_user()
        _add_usage(user, provider="openai", period="2026-01", tokens=1000)
        _add_usage(user, provider="openai", period="2026-01", tokens=500)
        _add_usage(user, provider="anthropic", period="2026-02", tokens=2000)

        estimates = br.estimate_by_provider_period(user_id=user.id)
        assert estimates[("openai", "2026-01")] == 1.5
        assert estimates[("anthropic", "2026-02")] == 2.0

    def test_ignores_user_messages(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 1.0
        user = _make_user()
        _add_usage(user, provider="openai", period="2026-01", tokens=1000, role="user")
        assert br.estimate_by_provider_period(user_id=user.id) == {}


class TestReconcile:
    def test_computes_variance_and_includes_estimates_without_records(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 1.0
        user = _make_user()
        _add_usage(user, provider="openai", period="2026-01", tokens=1000)
        records = [
            ProviderBillingRecord(
                user_id=user.id, provider="openai", period="2026-01", amount_usd=2.0
            ),
            ProviderBillingRecord(
                user_id=user.id, provider="anthropic", period="2026-01", amount_usd=5.0
            ),
        ]
        db.session.add_all(records)
        db.session.commit()

        rows = {r["provider"]: r for r in br.reconcile(records, user_id=user.id)}
        # openai reported 2.00 vs estimated 1.00 -> +1.00 / +100%.
        assert rows["openai"]["estimated_usd"] == 1.0
        assert rows["openai"]["variance_usd"] == 1.0
        assert rows["openai"]["variance_pct"] == 100.0
        # anthropic: reported 5.00, no estimate -> variance 5.00, pct None.
        assert rows["anthropic"]["estimated_usd"] == 0.0
        assert rows["anthropic"]["variance_pct"] is None
        assert rows["anthropic"]["has_record"] is True


class TestImport:
    def test_import_is_idempotent_and_never_overwrites_estimates(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 1.0
        user = _make_user()
        _add_usage(user, provider="openai", period="2026-01", tokens=1000)

        first = br.import_billing_csv(
            _fixture("providers_2026-01.csv"), user_id=user.id, source_filename="providers.csv"
        )
        db.session.commit()
        assert first["created"] == 3
        assert first["updated"] == 0
        assert ProviderBillingRecord.query.count() == 3

        estimate_after_first = br.estimate_by_provider_period(user_id=user.id)

        second = br.import_billing_csv(
            _fixture("providers_2026-01.csv"), user_id=user.id, source_filename="providers.csv"
        )
        db.session.commit()
        assert second["created"] == 0
        assert second["updated"] == 3
        assert ProviderBillingRecord.query.count() == 3

        # The estimate is derived from token usage and is unchanged by imports.
        assert br.estimate_by_provider_period(user_id=user.id) == estimate_after_first

    def test_update_replaces_the_amount_in_place(self, app):
        user = _make_user()
        br.import_billing_csv("provider,period,amount_usd\nopenai,2026-01,1.00\n", user_id=user.id)
        db.session.commit()
        br.import_billing_csv("provider,period,amount_usd\nopenai,2026-01,9.00\n", user_id=user.id)
        db.session.commit()

        record = ProviderBillingRecord.query.filter_by(user_id=user.id, provider="openai").one()
        assert record.amount_usd == 9.0

    def test_rejects_an_oversized_file(self, app):
        app.config["BILLING_IMPORT_MAX_BYTES"] = 10
        with pytest.raises(BillingImportError) as exc:
            br.import_billing_csv("provider,period,amount_usd\nopenai,2026-01,1\n", user_id=None)
        assert exc.value.kind == "size"


class TestReport:
    def test_report_totals(self, app):
        app.config["LLM_ESTIMATE_USD_PER_1K_TOKENS"] = 1.0
        user = _make_user()
        _add_usage(user, provider="openai", period="2026-01", tokens=1000)
        br.import_billing_csv(_fixture("providers_2026-01.csv"), user_id=user.id)
        db.session.commit()

        report = br.reconciliation_report(user_id=user.id, period="2026-01")
        assert report["reported_total_usd"] == 16.5
        assert report["estimated_total_usd"] == 1.0
        assert report["variance_usd"] == 15.5
        assert all(row["period"] == "2026-01" for row in report["rows"])


class TestRoutes:
    def test_page_requires_login(self, client):
        response = client.get("/billing/")
        assert response.status_code in (302, 401)

    def test_page_and_api_render(self, client, make_user, login):
        make_user(username="routebiller", email="routebiller@example.com")
        login(email="routebiller@example.com")
        assert client.get("/billing/").status_code == 200
        assert client.get("/billing/api/reconciliation").status_code == 200

    def test_import_json_round_trip(self, client, make_user, login, app):
        user = make_user(username="jsonbiller", email="jsonbiller@example.com")
        login(email="jsonbiller@example.com")

        response = client.post(
            "/billing/import",
            data={"csv": _fixture("providers_2026-01.csv")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["created"] == 3

        record = ProviderBillingRecord.query.filter_by(user_id=user.id).count()
        assert record == 3

    def test_import_file_upload_redirects(self, client, make_user, login, app):
        import io

        make_user(username="uploadbiller", email="uploadbiller@example.com")
        login(email="uploadbiller@example.com")

        response = client.post(
            "/billing/import",
            data={"file": (io.BytesIO(_fixture("providers_2026-01.csv").encode()), "billing.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Reconciliation" in response.data

    def test_import_rejects_a_bad_csv(self, client, make_user, login):
        make_user(username="badbiller", email="badbiller@example.com")
        login(email="badbiller@example.com")

        response = client.post(
            "/billing/import",
            data={"csv": _fixture("bad_period.csv")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 400
        assert response.get_json()["kind"] == "period"

    def test_import_is_owner_scoped(self, client, make_user, login, app):
        owner = make_user(username="ownerbiller", email="ownerbiller@example.com")
        login(email="ownerbiller@example.com")
        client.post(
            "/billing/import",
            data={"csv": _fixture("providers_2026-01.csv")},
            headers={"Accept": "application/json"},
        )

        make_user(username="otherbiller", email="otherbiller@example.com")
        login(email="otherbiller@example.com")
        report = client.get("/billing/api/reconciliation").get_json()

        assert report["records"] == []
        assert report["reported_total_usd"] == 0.0
        assert ProviderBillingRecord.query.filter_by(user_id=owner.id).count() == 3

    def test_import_writes_an_audit_entry(self, client, make_user, login, app):
        from app.models import AuditLog

        make_user(username="auditbiller", email="auditbiller@example.com")
        login(email="auditbiller@example.com")
        client.post(
            "/billing/import",
            data={"csv": _fixture("providers_2026-01.csv")},
            headers={"Accept": "application/json"},
        )

        entry = AuditLog.query.filter_by(action="provider_billing_imported").one()
        assert entry.metadata_dict["created"] == 3

    def test_reconciliation_period_filter_rejects_bad_period(self, client, make_user, login):
        make_user(username="periodbiller", email="periodbiller@example.com")
        login(email="periodbiller@example.com")
        response = client.get("/billing/api/reconciliation?period=2026-13")
        assert response.status_code == 400
