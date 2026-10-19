"""Tests for the documented default price table (issue #439)."""

import pytest

from app.services import pricing
from app.services.pricing import (
    DEFAULT_PRICE_TABLE,
    ESTIMATE_DISCLAIMER,
    MAX_TOKEN_COUNT,
    WILDCARD,
    ModelPrice,
    estimate_cost_usd,
    get_model_price,
    list_model_prices,
    price_table,
)
from app.services.providers.anthropic import DEFAULT_MODELS as ANTHROPIC_MODELS
from app.services.providers.mock import MockProvider
from app.services.providers.openai import DEFAULT_MODELS as OPENAI_MODELS


class TestDefaultTable:
    def test_mock_provider_is_free_and_not_an_estimate(self):
        price = get_model_price("mock", "mock-1")
        assert price is not None
        assert price.input_per_1k_usd == 0
        assert price.output_per_1k_usd == 0
        assert price.estimated is False
        assert estimate_cost_usd("mock", "mock-1", prompt_tokens=10_000) == 0

    @pytest.mark.parametrize("model", OPENAI_MODELS)
    def test_every_builtin_openai_model_has_a_default(self, model):
        price = get_model_price("openai", model)
        assert price is not None
        assert price.estimated is True
        assert price.input_per_1k_usd >= 0
        assert price.output_per_1k_usd >= 0

    @pytest.mark.parametrize("model", ANTHROPIC_MODELS)
    def test_every_builtin_anthropic_model_has_a_default(self, model):
        price = get_model_price("anthropic", model)
        assert price is not None
        assert price.estimated is True
        assert price.input_per_1k_usd >= 0
        assert price.output_per_1k_usd >= 0

    def test_registered_mock_provider_model_is_covered(self):
        assert get_model_price(MockProvider.name, MockProvider.models[0]) is not None

    def test_lookup_is_case_and_whitespace_insensitive(self):
        assert get_model_price("  OpenAI ", " GPT-4O-MINI ") == get_model_price(
            "openai", "gpt-4o-mini"
        )

    def test_unknown_provider_and_model_return_none(self):
        assert get_model_price("does-not-exist", "gpt-4o") is None
        assert get_model_price("openai", "gpt-0") is None
        assert get_model_price(None, None) is None

    def test_entries_are_documented_as_estimates(self):
        for price in DEFAULT_PRICE_TABLE.values():
            if price.provider == "mock":
                assert price.estimated is False
            else:
                assert price.estimated is True
                assert price.to_dict()["disclaimer"] == ESTIMATE_DISCLAIMER

    def test_listing_and_grouping(self):
        assert [p.provider for p in list_model_prices("mock")] == ["mock"]
        assert len(list_model_prices()) == len(DEFAULT_PRICE_TABLE)
        grouped = price_table()
        assert set(grouped) == {"mock", "openai", "anthropic"}
        assert grouped["mock"][0]["model"] == "mock-1"


class TestCostEstimate:
    def test_cost_uses_input_and_output_rates(self):
        price = get_model_price("openai", "gpt-4o")
        cost = estimate_cost_usd(
            "openai",
            "gpt-4o",
            prompt_tokens=2_000,
            completion_tokens=1_000,
        )
        expected = 2 * price.input_per_1k_usd + 1 * price.output_per_1k_usd
        assert cost == pytest.approx(expected)

    def test_zero_tokens_cost_nothing_for_known_models(self):
        assert estimate_cost_usd("openai", "gpt-4o") == 0

    def test_unknown_model_cost_is_none(self):
        assert estimate_cost_usd("openai", "unknown", prompt_tokens=10, completion_tokens=5) is None

    def test_negative_tokens_are_rejected(self):
        with pytest.raises(ValueError):
            estimate_cost_usd("openai", "gpt-4o", prompt_tokens=-1)
        with pytest.raises(ValueError):
            estimate_cost_usd("openai", "gpt-4o", completion_tokens=-1)


def _entry(provider, model, input_rate=1.0, output_rate=2.0):
    return ModelPrice(
        provider=provider,
        model=model,
        input_per_1k_usd=input_rate,
        output_per_1k_usd=output_rate,
    )


class TestLookupPrecedence:
    """Exact entries beat wildcards; provider fallbacks beat global ones."""

    @pytest.fixture()
    def table(self):
        return {
            ("openai", "gpt-4o"): _entry("openai", "gpt-4o", 1.0, 2.0),
            ("openai", WILDCARD): _entry("openai", WILDCARD, 3.0, 4.0),
            (WILDCARD, "gpt-4o"): _entry(WILDCARD, "gpt-4o", 5.0, 6.0),
            (WILDCARD, WILDCARD): _entry(WILDCARD, WILDCARD, 7.0, 8.0),
        }

    def test_exact_match_wins_over_every_wildcard(self, table):
        price = get_model_price("openai", "gpt-4o", table=table)
        assert price.input_per_1k_usd == 1.0
        assert price.output_per_1k_usd == 2.0

    def test_provider_wildcard_beats_model_and_global_wildcards(self, table):
        price = get_model_price("openai", "a-new-model", table=table)
        assert price.input_per_1k_usd == 3.0

    def test_model_wildcard_used_for_unknown_provider(self, table):
        price = get_model_price("other-provider", "gpt-4o", table=table)
        assert price.input_per_1k_usd == 5.0

    def test_global_wildcard_is_the_last_resort(self, table):
        price = get_model_price("other-provider", "other-model", table=table)
        assert price.input_per_1k_usd == 7.0

    def test_lookup_still_normalizes_case_and_whitespace(self, table):
        assert get_model_price("  OpenAI ", " GPT-4O ", table=table) == get_model_price(
            "openai", "gpt-4o", table=table
        )

    def test_unknown_model_without_a_wildcard_is_none(self):
        assert get_model_price("does-not-exist", "gpt-4o", table={}) is None

    def test_custom_table_listing_and_grouping(self, table):
        assert len(list_model_prices("openai", table=table)) == 2
        grouped = price_table(table=table)
        assert "openai" in grouped
        assert {entry["model"] for entry in grouped["openai"]} == {"gpt-4o", WILDCARD}


class TestCostArithmeticAndClamping:
    def test_arithmetic_is_pinned_to_the_price_rates(self):
        table = {("acme", "model"): _entry("acme", "model", 0.5, 1.5)}
        cost = estimate_cost_usd(
            "acme",
            "model",
            prompt_tokens=1_234,
            completion_tokens=5_678,
            table=table,
        )
        expected = 1_234 / 1000 * 0.5 + 5_678 / 1000 * 1.5
        assert cost == pytest.approx(expected, abs=1e-12)

    def test_wildcard_estimate_uses_the_fallback_rate(self):
        table = {
            ("openai", WILDCARD): _entry("openai", WILDCARD, 0.25, 0.75),
        }
        cost = estimate_cost_usd(
            "openai",
            "brand-new-model",
            prompt_tokens=2_000,
            completion_tokens=1_000,
            table=table,
        )
        assert cost == pytest.approx(2 * 0.25 + 1 * 0.75)

    def test_unknown_model_estimate_is_none(self):
        assert estimate_cost_usd("acme", "missing", prompt_tokens=10, table={}) is None

    def test_token_counts_are_clamped_to_the_maximum(self):
        price = get_model_price("openai", "gpt-4o")
        at_limit = estimate_cost_usd(
            "openai",
            "gpt-4o",
            prompt_tokens=MAX_TOKEN_COUNT,
            completion_tokens=0,
        )
        above_limit = estimate_cost_usd(
            "openai",
            "gpt-4o",
            prompt_tokens=MAX_TOKEN_COUNT + 1_000_000,
            completion_tokens=0,
        )
        assert above_limit == pytest.approx(at_limit)
        assert above_limit == pytest.approx(MAX_TOKEN_COUNT / 1000 * price.input_per_1k_usd)

    def test_completion_count_is_clamped_independently(self):
        price = get_model_price("openai", "gpt-4o")
        cost = estimate_cost_usd(
            "openai",
            "gpt-4o",
            prompt_tokens=0,
            completion_tokens=MAX_TOKEN_COUNT * 5,
        )
        assert cost == pytest.approx(MAX_TOKEN_COUNT / 1000 * price.output_per_1k_usd)


class TestWorkspacePriceOverrides:
    """Per-workspace overrides take precedence over the global table (#442)."""

    def _workspace(self, db, make_user, name="Pricing"):
        from app.models import Workspace

        user = make_user(username="price-owner", email="price-owner@example.com")
        workspace = Workspace(user_id=user.id, name=name)
        db.session.add(workspace)
        db.session.commit()
        return user, workspace

    def test_override_wins_over_global_exact_price(self, db, make_user):
        user, workspace = self._workspace(db, make_user)
        pricing.set_workspace_price_override(
            workspace=workspace,
            provider="OpenAI",
            model="GPT-4O",
            input_per_1k_usd=9.0,
            output_per_1k_usd=11.0,
            user=user,
        )

        global_price = pricing.get_model_price("openai", "gpt-4o")
        effective = pricing.get_model_price("openai", "gpt-4o", workspace_id=workspace.id)

        assert effective is not None
        assert effective.input_per_1k_usd == 9.0
        assert effective.output_per_1k_usd == 11.0
        assert effective.estimated is False
        assert effective != global_price

    def test_global_price_is_the_fallback_when_no_override_exists(self, db, make_user):
        _, workspace = self._workspace(db, make_user)

        effective = pricing.get_model_price("openai", "gpt-4o", workspace_id=workspace.id)

        assert effective == pricing.get_model_price("openai", "gpt-4o")

    def test_workspace_wildcard_beats_global_exact_and_provider_fallbacks(self, db, make_user):
        user, workspace = self._workspace(db, make_user)
        pricing.set_workspace_price_override(
            workspace=workspace,
            provider="openai",
            model="*",
            input_per_1k_usd=1.5,
            output_per_1k_usd=2.5,
            user=user,
        )

        assert (
            pricing.get_model_price("openai", "gpt-4o", workspace_id=workspace.id).input_per_1k_usd
            == 1.5
        )
        assert (
            pricing.get_model_price(
                "openai", "a-new-model", workspace_id=workspace.id
            ).input_per_1k_usd
            == 1.5
        )
        # A different provider still falls back to the global table.
        assert pricing.get_model_price(
            "anthropic", "claude-3-5-sonnet-latest", workspace_id=workspace.id
        ) == pricing.get_model_price("anthropic", "claude-3-5-sonnet-latest")

    def test_estimate_uses_workspace_rates(self, db, make_user):
        user, workspace = self._workspace(db, make_user)
        pricing.set_workspace_price_override(
            workspace=workspace,
            provider="openai",
            model="gpt-4o",
            input_per_1k_usd=2.0,
            output_per_1k_usd=4.0,
            user=user,
        )

        cost = pricing.estimate_cost_usd(
            "openai",
            "gpt-4o",
            prompt_tokens=1_000,
            completion_tokens=500,
            workspace_id=workspace.id,
        )

        assert cost == pytest.approx(2.0 + 2.0)

    def test_upsert_updates_one_row_and_delete_removes_it(self, db, make_user):
        from app.models import WorkspacePriceOverride

        user, workspace = self._workspace(db, make_user)
        pricing.set_workspace_price_override(workspace, "openai", "gpt-4o", 1.0, 2.0, user=user)
        pricing.set_workspace_price_override(workspace, "openai", "gpt-4o", 3.0, 4.0, user=user)

        rows = pricing.list_workspace_price_overrides(workspace.id, user=user)
        assert len(rows) == 1
        assert rows[0].input_per_1k_usd == 3.0
        assert WorkspacePriceOverride.query.count() == 1

        assert (
            pricing.delete_workspace_price_override(workspace, "openai", "gpt-4o", user=user)
            is True
        )
        assert WorkspacePriceOverride.query.count() == 0
        assert (
            pricing.delete_workspace_price_override(workspace, "openai", "gpt-4o", user=user)
            is False
        )

    def test_overrides_are_owner_scoped(self, db, make_user):
        from app.models import WorkspaceMember

        owner, workspace = self._workspace(db, make_user)
        member = make_user(username="price-member", email="price-member@example.com")
        db.session.add(WorkspaceMember(workspace_id=workspace.id, user_id=member.id))
        db.session.commit()

        with pytest.raises(PermissionError):
            pricing.set_workspace_price_override(
                workspace, "openai", "gpt-4o", 1.0, 2.0, user=member
            )
        with pytest.raises(PermissionError):
            pricing.list_workspace_price_overrides(workspace.id, user=member)

        # The owner can still manage the same workspace.
        row = pricing.set_workspace_price_override(
            workspace, "openai", "gpt-4o", 1.0, 2.0, user=owner
        )
        assert row.workspace_id == workspace.id

    def test_invalid_rates_and_keys_are_rejected(self, db, make_user):
        user, workspace = self._workspace(db, make_user)

        with pytest.raises(pricing.PriceOverrideError):
            pricing.set_workspace_price_override(workspace, "", "gpt-4o", 1, 2, user=user)
        with pytest.raises(pricing.PriceOverrideError):
            pricing.set_workspace_price_override(workspace, "openai", "gpt-4o", -1, 2, user=user)
        with pytest.raises(pricing.PriceOverrideError):
            pricing.set_workspace_price_override(
                workspace, "openai", "gpt-4o", 1, float("inf"), user=user
            )

    def test_price_table_for_workspace_includes_overrides(self, db, make_user):
        user, workspace = self._workspace(db, make_user)
        pricing.set_workspace_price_override(workspace, "openai", "gpt-4o", 7.0, 8.0, user=user)

        grouped = pricing.price_table(workspace_id=workspace.id)
        entry = next(item for item in grouped["openai"] if item["model"] == "gpt-4o")

        assert entry["input_per_1k_usd"] == 7.0
        assert entry["output_per_1k_usd"] == 8.0
