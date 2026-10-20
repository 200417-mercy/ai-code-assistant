"""Budget CRUD and authorization (Phase 10, issues #461 and #468).

Every operation takes the acting ``user`` and enforces the shared billing access
rules from :mod:`app.services.budget_access`. Inaccessible ids raise the same
:class:`BudgetNotFoundError` as missing ids, so callers cannot distinguish the
two. Validation errors raise :class:`BudgetValidationError`.
"""

from __future__ import annotations

import math
import re

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import Budget, Workspace
from app.models.budget import BUDGET_PERIODS
from app.services.budget_access import can_access_budget, can_access_workspace_billing

#: Largest accepted limit, to keep arithmetic finite and dashboards bounded.
MAX_LIMIT_AMOUNT = 1_000_000_000_000.0
MAX_NAME_LENGTH = 200
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


class BudgetError(ValueError):
    """A budget payload is invalid."""


class BudgetValidationError(BudgetError):
    """A field failed validation; safe to return to the caller."""


class BudgetNotFoundError(LookupError):
    """The budget does not exist or the caller may not access it."""

    def __init__(self, message: str = "Budget not found."):
        super().__init__(message)


def _coerce_limit(value) -> float:
    if value is None or isinstance(value, bool):
        raise BudgetValidationError("limit_amount is required and must be a number.")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise BudgetValidationError("limit_amount must be a number.") from exc
    if not math.isfinite(amount):
        raise BudgetValidationError("limit_amount must be finite.")
    if amount <= 0:
        raise BudgetValidationError("limit_amount must be greater than zero.")
    if amount > MAX_LIMIT_AMOUNT:
        raise BudgetValidationError("limit_amount is too large.")
    return amount


def _coerce_name(value) -> str:
    if value is None:
        return "Budget"
    if not isinstance(value, str):
        raise BudgetValidationError("name must be a string.")
    name = value.strip()
    if not name:
        raise BudgetValidationError("name must not be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise BudgetValidationError(f"name must be at most {MAX_NAME_LENGTH} characters.")
    return name


def _coerce_period(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BudgetValidationError("period is required.")
    period = value.strip().lower()
    if period not in BUDGET_PERIODS:
        raise BudgetValidationError(f"period must be one of: {', '.join(BUDGET_PERIODS)}.")
    return period


def _coerce_currency(value) -> str:
    if value is None:
        return "USD"
    if not isinstance(value, str) or not _CURRENCY_RE.match(value.strip()):
        raise BudgetValidationError("currency must be a three-letter code.")
    return value.strip().upper()


def _coerce_workspace_id(value) -> int:
    if isinstance(value, bool) or value is None:
        raise BudgetValidationError("workspace_id must be a positive integer.")
    try:
        workspace_id = int(value)
    except (TypeError, ValueError) as exc:
        raise BudgetValidationError("workspace_id must be a positive integer.") from exc
    if workspace_id <= 0:
        raise BudgetValidationError("workspace_id must be a positive integer.")
    return workspace_id


def create_budget(user, payload: dict) -> Budget:
    """Create a user or workspace budget for ``user``.

    Creating a workspace budget requires workspace-owner or site-admin access.
    An inaccessible workspace is reported exactly like a missing one.
    """
    if not isinstance(payload, dict):
        raise BudgetValidationError("A JSON object body is required.")

    workspace_id = None
    if payload.get("workspace_id") is not None:
        workspace_id = _coerce_workspace_id(payload.get("workspace_id"))
        if not can_access_workspace_billing(workspace_id, user):
            raise BudgetNotFoundError()

    budget = Budget(
        user_id=user.id,
        workspace_id=workspace_id,
        name=_coerce_name(payload.get("name")),
        limit_amount=_coerce_limit(payload.get("limit_amount")),
        currency=_coerce_currency(payload.get("currency")),
        period=_coerce_period(payload.get("period")),
    )
    db.session.add(budget)
    db.session.commit()
    return budget


def get_budget(budget_id: int, user) -> Budget:
    """Return an accessible budget or raise :class:`BudgetNotFoundError`."""
    budget = db.session.get(Budget, budget_id)
    if budget is None or not can_access_budget(budget, user):
        raise BudgetNotFoundError()
    return budget


def list_budgets(user) -> list[Budget]:
    """Return the budgets ``user`` may access, in stable creation order.

    User budgets are always restricted to their owner. Workspace budgets are
    visible to the workspace owner and to site administrators.
    """
    own_user_budget = and_(
        Budget.user_id == user.id,
        Budget.workspace_id.is_(None),
    )
    if bool(getattr(user, "is_admin", False)):
        accessible = or_(Budget.workspace_id.is_not(None), own_user_budget)
    else:
        owned_workspaces = db.session.query(Workspace.id).filter(Workspace.user_id == user.id)
        accessible = or_(
            own_user_budget,
            Budget.workspace_id.in_(owned_workspaces),
        )
    return Budget.query.filter(accessible).order_by(Budget.created_at, Budget.id).all()


def update_budget(budget_id: int, user, payload: dict) -> Budget:
    """Update an accessible budget.

    The scope (``user_id``/``workspace_id``) is immutable: moving a budget
    between owners or workspaces is not a supported operation.
    """
    if not isinstance(payload, dict):
        raise BudgetValidationError("A JSON object body is required.")
    budget = get_budget(budget_id, user)

    if "workspace_id" in payload and payload.get("workspace_id") != budget.workspace_id:
        raise BudgetValidationError("A budget's scope cannot be changed.")
    if "user_id" in payload and payload.get("user_id") != budget.user_id:
        raise BudgetValidationError("A budget's owner cannot be changed.")

    if "name" in payload:
        budget.name = _coerce_name(payload.get("name"))
    if "limit_amount" in payload:
        budget.limit_amount = _coerce_limit(payload.get("limit_amount"))
    if "currency" in payload:
        budget.currency = _coerce_currency(payload.get("currency"))
    if "period" in payload:
        budget.period = _coerce_period(payload.get("period"))

    db.session.commit()
    return budget


def delete_budget(budget_id: int, user) -> None:
    """Delete an accessible budget, or raise :class:`BudgetNotFoundError`."""
    budget = get_budget(budget_id, user)
    db.session.delete(budget)
    db.session.commit()


__all__ = [
    "MAX_LIMIT_AMOUNT",
    "BudgetError",
    "BudgetNotFoundError",
    "BudgetValidationError",
    "create_budget",
    "delete_budget",
    "get_budget",
    "list_budgets",
    "update_budget",
]
