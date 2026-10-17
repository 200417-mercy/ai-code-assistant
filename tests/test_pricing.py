"""Tests for the documented default price table (issue #439)."""

import pytest

from app.services.pricing import (
    DEFAULT_PRICE_TABLE,
    ESTIMATE_DISCLAIMER,
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
