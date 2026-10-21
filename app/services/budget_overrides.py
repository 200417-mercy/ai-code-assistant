"""Time-bounded workspace budget overrides (Phase 10, issue #467).

An override is only effective between ``starts_at`` (inclusive) and
``expires_at`` (exclusive). Creating one records an audit entry containing the
actor and the reason, so a temporary increase can always be traced back.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.extensions import db
from app.models.workspace_budget_override import WorkspaceBudgetOverride
from app.services import audit


class BudgetOverrideError(ValueError):
    """Raised when an override request is invalid or unauthorized."""


def _resolve_workspace(workspace=None, workspace_id=None):
    from app.models import Workspace

    if workspace is not None:
        return workspace
    if workspace_id is None:
        raise BudgetOverrideError("A workspace is required.")
    resolved = db.session.get(Workspace, workspace_id)
    if resolved is None:
        raise BudgetOverrideError("Workspace not found.")
    return resolved


def _assert_owner(workspace, user) -> None:
    if user is None:
        return
    if getattr(user, "id", user) != workspace.user_id:
        raise BudgetOverrideError("Only the workspace owner may change its budget.")


def _aware(value, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise BudgetOverrideError(f"{name} must be a datetime.")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _positive_limit(value) -> float:
    try:
        limit = float(value)
    except (TypeError, ValueError) as exc:
        raise BudgetOverrideError("limit_amount must be a number.") from exc
    if not math.isfinite(limit) or limit <= 0:
        raise BudgetOverrideError("limit_amount must be greater than zero.")
    return limit


def create_budget_override(
    workspace=None,
    *,
    workspace_id=None,
    limit,
    starts_at,
    expires_at,
    reason,
    user=None,
    commit=True,
):
    """Create a time-bounded budget override and audit who set it and why."""
    resolved = _resolve_workspace(workspace, workspace_id)
    _assert_owner(resolved, user)

    limit_value = _positive_limit(limit)
    start = _aware(starts_at, "starts_at")
    end = _aware(expires_at, "expires_at")
    if end <= start:
        raise BudgetOverrideError("expires_at must be after starts_at.")

    reason_text = str(reason or "").strip()
    if not reason_text:
        raise BudgetOverrideError("A reason is required.")
    if len(reason_text) > 500:
        raise BudgetOverrideError("reason must be at most 500 characters.")

    prior = active_budget_override(resolved)
    row = WorkspaceBudgetOverride(
        workspace_id=resolved.id,
        created_by_id=getattr(user, "id", user),
        limit_amount=limit_value,
        starts_at=start,
        expires_at=end,
        reason=reason_text,
    )
    db.session.add(row)
    db.session.flush()

    audit.record(
        audit.BUDGET_OVERRIDE_CREATED,
        user=user if hasattr(user, "id") else None,
        user_id=None if hasattr(user, "id") else user,
        target_type="workspace",
        target_id=resolved.id,
        metadata={
            "override_id": row.id,
            "limit_amount": limit_value,
            "starts_at": start.isoformat(),
            "expires_at": end.isoformat(),
            "reason": reason_text,
            "diff": {
                "limit_amount": {
                    "before": prior.limit_amount if prior is not None else None,
                    "after": limit_value,
                }
            },
        },
    )

    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return row


def list_budget_overrides(workspace=None, *, workspace_id=None) -> list:
    """Return a workspace's overrides, newest window first."""
    resolved = _resolve_workspace(workspace, workspace_id)
    return (
        WorkspaceBudgetOverride.query.filter_by(workspace_id=resolved.id)
        .order_by(WorkspaceBudgetOverride.starts_at.desc(), WorkspaceBudgetOverride.id.desc())
        .all()
    )


def active_budget_override(workspace=None, *, workspace_id=None, at=None):
    """Return the override covering ``at``, or ``None`` when none is active."""
    resolved = _resolve_workspace(workspace, workspace_id)
    moment = _aware(at or datetime.now(UTC), "at")
    return (
        WorkspaceBudgetOverride.query.filter(
            WorkspaceBudgetOverride.workspace_id == resolved.id,
            WorkspaceBudgetOverride.starts_at <= moment,
            WorkspaceBudgetOverride.expires_at > moment,
        )
        .order_by(WorkspaceBudgetOverride.starts_at.desc(), WorkspaceBudgetOverride.id.desc())
        .first()
    )


def effective_budget_limit(workspace=None, *, workspace_id=None, default=None, at=None):
    """Return the active override's limit, or ``default`` outside its window."""
    override = active_budget_override(workspace, workspace_id=workspace_id, at=at)
    return override.limit_amount if override is not None else default


__all__ = [
    "BudgetOverrideError",
    "active_budget_override",
    "create_budget_override",
    "effective_budget_limit",
    "list_budget_overrides",
]
