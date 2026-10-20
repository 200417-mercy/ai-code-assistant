"""Billing access rules shared by budgets and usage dashboards (#468).

The rules are intentionally small and centralized so every caller — API routes,
services, and dashboards — uses the same check:

* a **user** resource is accessible only to its owner;
* a **workspace** resource is accessible to the workspace owner and to site
  administrators.

Non-members and members without the required role always fail closed. Callers
render inaccessible and missing resources identically, so ids cannot be probed.
"""

from __future__ import annotations

from app.models import Workspace


def is_authenticated(user) -> bool:
    """Return ``True`` when ``user`` is a signed-in account."""
    return bool(user is not None and getattr(user, "is_authenticated", False))


def can_access_user_billing(user_id: int, user) -> bool:
    """Return ``True`` when ``user`` owns the user-scoped billing resource."""
    return is_authenticated(user) and getattr(user, "id", None) == user_id


def can_access_workspace_billing(workspace_id: int, user) -> bool:
    """Return ``True`` for the workspace owner or a site administrator."""
    if workspace_id is None or not is_authenticated(user):
        return False
    workspace = Workspace.query.filter_by(id=workspace_id).first()
    if workspace is None:
        return False
    return workspace.user_id == user.id or bool(getattr(user, "is_admin", False))


def can_access_budget(budget, user) -> bool:
    """Return ``True`` when ``user`` may read or modify ``budget``."""
    if budget is None or not is_authenticated(user):
        return False
    if getattr(budget, "workspace_id", None) is not None:
        return can_access_workspace_billing(budget.workspace_id, user)
    return can_access_user_billing(budget.user_id, user)
