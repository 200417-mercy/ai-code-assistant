"""Budget evaluation and enforcement (Phase 10)."""

from __future__ import annotations

from app.models.budget import Budget

WARNING_RATIO = 0.8
EXCEEDED_RATIO = 1.0


class BudgetError(ValueError):
    """Base class for invalid budget operations."""


class BudgetExceededError(BudgetError):
    """Raised when a spend would exceed a configured limit."""


def _validate_spend(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetError(f"{name} must be numeric")
    if value < 0:
        raise BudgetError(f"{name} must be non-negative")
    return float(value)


def _ratio(limit, spend) -> float | None:
    if limit is None:
        return None
    if limit < 0:
        raise BudgetError("budget limits must be non-negative")
    if limit == 0:
        return 0.0 if spend == 0 else float("inf")
    return spend / limit


def evaluate_budget(budget: Budget, *, token_spend=0, cost_spend=0.0) -> dict:
    """Evaluate usage against a budget without mutating it."""
    if not isinstance(budget, Budget):
        raise BudgetError("budget must be a Budget")
    tokens = _validate_spend(token_spend, "token_spend")
    cost = _validate_spend(cost_spend, "cost_spend")
    token_ratio = _ratio(budget.token_limit, tokens)
    cost_ratio = _ratio(budget.cost_limit, cost)
    ratios = [r for r in (token_ratio, cost_ratio) if r is not None]
    used_ratio = max(ratios, default=0.0)
    if used_ratio > EXCEEDED_RATIO:
        status = "exceeded"
    elif used_ratio >= WARNING_RATIO:
        status = "warning"
    else:
        status = "ok"
    return {
        "budget_id": budget.id,
        "period": budget.period,
        "token_limit": budget.token_limit,
        "cost_limit": budget.cost_limit,
        "token_spend": tokens,
        "cost_spend": cost,
        "token_remaining": (
            None if budget.token_limit is None else max(0, budget.token_limit - tokens)
        ),
        "cost_remaining": None if budget.cost_limit is None else max(0.0, budget.cost_limit - cost),
        "used_ratio": used_ratio,
        "status": status,
        "exceeded": status == "exceeded",
    }


def enforce_budget(budget: Budget, *, token_spend=0, cost_spend=0.0) -> dict:
    """Return the evaluation, or raise when the budget is exceeded."""
    result = evaluate_budget(budget, token_spend=token_spend, cost_spend=cost_spend)
    if result["exceeded"]:
        raise BudgetExceededError(
            f"budget {budget.id} exceeded for period {budget.period}: "
            f"tokens={token_spend}, cost={cost_spend}"
        )
    return result


__all__ = [
    "EXCEEDED_RATIO",
    "WARNING_RATIO",
    "BudgetError",
    "BudgetExceededError",
    "enforce_budget",
    "evaluate_budget",
]
