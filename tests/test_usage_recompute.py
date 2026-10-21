"""Tests for historical cost recomputation (#440)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.extensions import db as database
from app.models import UsageRecord
from app.services import usage_ledger


def _record(user, *, provider="openai", model="gpt-4o-mini", cost=999.0, **kwargs):
    return usage_ledger.record_usage(
        source=kwargs.pop("source", "chat"),
        user=user,
        provider=provider,
        model=model,
        prompt_tokens=kwargs.pop("prompt_tokens", 1000),
        completion_tokens=kwargs.pop("completion_tokens", 0),
        cost_estimate=cost,
        **kwargs,
    )


class TestRecomputeCosts:
    def test_recomputes_changed_rows_and_records_the_timestamp(self, db, make_user):
        user = make_user(username="recompute", email="recompute@example.com")
        stale = _record(user)
        free = _record(user, provider="mock", model="mock-1", cost=0.0)

        result = usage_ledger.recompute_cost_estimates(user_id=user.id)

        assert result["scanned"] == 2
        assert result["updated"] == 1
        assert result["unchanged"] == 1
        database.session.refresh(stale)
        database.session.refresh(free)
        assert stale.cost_estimate == 0.00015
        assert stale.cost_recomputed_at is not None
        assert free.cost_estimate == 0.0
        assert free.cost_recomputed_at is None

    def test_unknown_prices_become_none_not_zero(self, db, make_user):
        user = make_user(username="unknown", email="unknown@example.com")
        row = _record(user, provider="no-such-provider", model="no-such-model", cost=5.0)

        result = usage_ledger.recompute_cost_estimates(user_id=user.id)

        assert result["unpriced"] == 1
        assert result["updated"] == 1
        database.session.refresh(row)
        assert row.cost_estimate is None
        assert row.cost_recomputed_at is not None

    def test_is_idempotent(self, db, make_user):
        user = make_user(username="idempotent", email="idempotent@example.com")
        row = _record(user)

        first = usage_ledger.recompute_cost_estimates(user_id=user.id)
        database.session.refresh(row)
        first_timestamp = row.cost_recomputed_at
        second = usage_ledger.recompute_cost_estimates(user_id=user.id)
        database.session.refresh(row)

        assert first["updated"] == 1
        assert second["updated"] == 0
        assert second["unchanged"] == 1
        assert row.cost_recomputed_at == first_timestamp

    def test_dry_run_does_not_write(self, db, make_user):
        user = make_user(username="dry", email="dry@example.com")
        row = _record(user)

        result = usage_ledger.recompute_cost_estimates(user_id=user.id, dry_run=True)

        assert result["updated"] == 1
        assert result["dry_run"] is True
        database.session.refresh(row)
        assert row.cost_estimate == 999.0
        assert row.cost_recomputed_at is None

    def test_scopes_and_bounds_the_batch(self, db, make_user):
        owner = make_user(username="owner", email="owner@example.com")
        other = make_user(username="other", email="other@example.com")
        first = _record(owner, prompt_tokens=1000)
        _record(owner, prompt_tokens=2000)
        _record(other, prompt_tokens=3000)

        result = usage_ledger.recompute_cost_estimates(user_id=owner.id, batch_size=1, max_rows=1)

        assert result["scanned"] == 1
        assert result["updated"] == 1
        assert result["batches"] == 1
        database.session.refresh(first)
        assert first.cost_recomputed_at is not None
        assert UsageRecord.query.filter(UsageRecord.cost_recomputed_at.isnot(None)).count() == 1

    def test_cli_emits_json_and_is_idempotent(self, app, db, make_user):
        user = make_user(username="cli", email="cli@example.com")
        _record(user)
        runner = app.test_cli_runner()

        first = runner.invoke(args=["usage", "recompute", "--json", "--limit", "10"])
        assert first.exit_code == 0, first.output
        payload = json.loads(first.output)
        assert payload["updated"] == 1

        second = runner.invoke(args=["usage", "recompute", "--json", "--limit", "10"])
        assert second.exit_code == 0, second.output
        assert json.loads(second.output)["updated"] == 0

    def test_recomputed_timestamp_is_serialized(self, db, make_user):
        user = make_user(username="serialized", email="serialized@example.com")
        row = _record(user)
        usage_ledger.recompute_cost_estimates(
            user_id=user.id, recomputed_at=datetime(2026, 10, 21, tzinfo=UTC)
        )
        database.session.refresh(row)

        assert row.to_dict()["cost_recomputed_at"].startswith("2026-10-21T00:00:00")
