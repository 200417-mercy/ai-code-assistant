"""Tests for provider/model usage breakdowns (#450)."""

from __future__ import annotations

from app.services import usage_ledger


def _record(user, *, provider, model, prompt, completion=0, cost=None, **kwargs):
    return usage_ledger.record_usage(
        source=kwargs.pop("source", "chat"),
        user=user,
        provider=provider,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_estimate=cost,
        **kwargs,
    )


class TestBreakdownByDimension:
    def test_groups_multiple_models_and_sorts_by_cost(self, db, make_user):
        user = make_user(username="models", email="models@example.com")
        _record(user, provider="openai", model="gpt-4o", prompt=1000, cost=0.5)
        _record(user, provider="openai", model="gpt-4o", prompt=500, cost=0.25)
        _record(user, provider="openai", model="gpt-4o-mini", prompt=100, cost=0.01)
        _record(user, provider="anthropic", model=None, prompt=50, cost=0.02)

        records = usage_ledger.query_records(user_id=user.id)
        providers, provider_groups, provider_truncated = usage_ledger.breakdown_by_dimension(
            records, "provider", limit=10
        )
        models, model_groups, model_truncated = usage_ledger.breakdown_by_dimension(
            records, "model", limit=10
        )

        assert provider_groups == 2
        assert provider_truncated is False
        assert [row["provider"] for row in providers] == ["openai", "anthropic"]
        assert providers[0]["count"] == 3
        assert providers[0]["total_tokens"] == 1600

        assert model_groups == 3
        assert model_truncated is False
        assert [row["model"] for row in models] == ["gpt-4o", "unknown", "gpt-4o-mini"]
        unknown = next(row for row in models if row["model"] == "unknown")
        assert unknown["count"] == 1
        assert unknown["total_tokens"] == 50

    def test_limit_folds_the_remainder_into_other(self, db, make_user):
        user = make_user(username="group-limit", email="limit@example.com")
        for index, cost in enumerate([0.5, 0.4, 0.3, 0.2, 0.1]):
            _record(user, provider=f"p{index}", model=f"m{index}", prompt=1, cost=cost)

        records = usage_ledger.query_records(user_id=user.id)
        rows, total_groups, truncated = usage_ledger.breakdown_by_dimension(
            records, "provider", limit=2
        )

        assert total_groups == 5
        assert truncated is True
        assert [row["provider"] for row in rows] == ["p0", "p1", "other"]
        assert rows[-1]["count"] == 3
        assert rows[-1]["cost_estimate"] == 0.6

    def test_blank_labels_group_as_unknown(self, db, make_user):
        user = make_user(username="blank", email="blank@example.com")
        _record(user, provider=None, model="", prompt=1, cost=0.0)

        records = usage_ledger.query_records(user_id=user.id)
        rows, _, _ = usage_ledger.breakdown_by_dimension(records, "provider", limit=5)

        assert rows[0]["provider"] == "unknown"
        assert rows[0]["count"] == 1

    def test_empty_scope_returns_empty_lists(self, db, make_user):
        user = make_user(username="empty", email="empty@example.com")
        payload = usage_ledger.breakdown_payload(user_id=user.id)

        assert payload["by_provider"] == []
        assert payload["by_model"] == []
        assert payload["provider_groups_total"] == 0
        assert payload["model_groups_total"] == 0
        assert payload["totals"]["count"] == 0


class TestBreakdownApi:
    def test_api_returns_sorted_provider_and_model_lists(self, client, db, make_user, login):
        user = make_user(username="api", email="api@example.com")
        _record(user, provider="openai", model="gpt-4o", prompt=1000, cost=0.5)
        _record(user, provider="anthropic", model="claude", prompt=10, cost=0.01)
        login(email="api@example.com")

        response = client.get("/api/usage/breakdown")
        assert response.status_code == 200
        payload = response.get_json()

        assert [row["provider"] for row in payload["by_provider"]] == [
            "openai",
            "anthropic",
        ]
        assert [row["model"] for row in payload["by_model"]] == ["gpt-4o", "claude"]
        assert payload["providers"] == payload["by_provider"]
        assert payload["models"] == payload["by_model"]
        assert payload["group_limit"] == usage_ledger.BREAKDOWN_DEFAULT_LIMIT
        assert payload["max_group_limit"] == usage_ledger.BREAKDOWN_MAX_LIMIT
        assert payload["cost_is_estimate"] is True

    def test_api_limit_is_clamped_and_truncation_is_reported(self, client, db, make_user, login):
        user = make_user(username="api-limit", email="api-limit@example.com")
        for index in range(4):
            _record(
                user,
                provider=f"provider-{index}",
                model=f"model-{index}",
                prompt=1,
                cost=1.0 - index / 10,
            )
        login(email="api-limit@example.com")

        payload = client.get("/api/usage/breakdown?limit=2").get_json()
        assert len(payload["by_provider"]) == 3  # two + other
        assert payload["provider_truncated"] is True
        assert payload["provider_groups_total"] == 4

        clamped = client.get("/api/usage/breakdown?limit=9999").get_json()
        assert clamped["group_limit"] == usage_ledger.BREAKDOWN_MAX_LIMIT
        assert clamped["provider_truncated"] is False

    def test_api_is_scoped_to_the_current_user(self, client, db, make_user, login):
        owner = make_user(username="owner", email="owner@example.com")
        other = make_user(username="other", email="other@example.com")
        _record(owner, provider="openai", model="gpt-4o", prompt=10, cost=0.1)
        _record(other, provider="secret", model="other-model", prompt=999, cost=99.0)
        login(email="owner@example.com")

        payload = client.get("/api/usage/breakdown").get_json()
        assert [row["provider"] for row in payload["by_provider"]] == ["openai"]
        assert all(row["provider"] != "secret" for row in payload["by_provider"])

    def test_requires_authentication(self, client):
        assert client.get("/api/usage/breakdown").status_code == 401
