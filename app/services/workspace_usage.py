"""Workspace-wide usage aggregation (Phase 10, issue #447).

The user-facing ``/api/usage`` endpoints are deliberately user-scoped: a
workspace member must never be able to infer another member's spend through
them. This service adds an explicit workspace dashboard with a role-aware
detail level:

* owners see totals and a per-member breakdown,
* contributors/viewers see workspace totals and their *own* member row,
* non-members are rejected by the route before this module is called.

Denied ledger rows are excluded, matching budget accounting: a refused call
never consumed tokens or cost.
"""

from __future__ import annotations

from sqlalchemy import func, or_

from app.models import Project, UsageRecord, User, WorkspaceMember
from app.services import pricing, usage_ledger

DENIED_STATUS = "denied"


def _base_query(workspace_id: int):
    """Ledger rows charged to a workspace, excluding refused calls."""
    return UsageRecord.query.filter(
        UsageRecord.workspace_id == workspace_id,
        or_(
            UsageRecord.status.is_(None),
            UsageRecord.status != DENIED_STATUS,
        ),
    )


def _group_totals(query, key_column) -> list[dict]:
    """Group a ledger query by ``key_column`` and return usage summaries."""
    rows = (
        query.with_entities(
            key_column.label("key"),
            func.count(UsageRecord.id).label("count"),
            func.coalesce(func.sum(UsageRecord.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageRecord.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(UsageRecord.cost_estimate), 0.0).label("cost"),
        )
        .group_by(key_column)
        .all()
    )
    return [
        {
            "key": row.key,
            "totals": {
                "count": int(row.count or 0),
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "cost_estimate": pricing.round_cost(float(row.cost or 0.0)),
            },
        }
        for row in rows
    ]


def _workspace_role(workspace, user) -> str | None:
    from app.services import permissions

    return permissions.role_for(workspace.id, user)


def workspace_usage(workspace, user) -> dict:
    """Aggregate totals, projects, sources and (role-permitting) members.

    ``workspace`` is a workspace the caller can already access; the route is
    responsible for the member check. The returned ``member_detail`` field is
    ``"all"`` for owners and ``"self"`` for other members.
    """
    role = _workspace_role(workspace, user)
    if role is None:
        # Defence in depth: never return usage to a non-member even if a caller
        # reaches this service directly.
        raise PermissionError("not a workspace member")

    base = _base_query(workspace.id)
    totals = usage_ledger.summarize_query(base)

    projects = {project.id: project for project in Project.query.all()}
    by_project = []
    for entry in _group_totals(base, UsageRecord.project_id):
        project = projects.get(entry["key"])
        by_project.append(
            {
                "project_id": entry["key"],
                "name": project.name if project is not None else "Unassigned",
                "totals": entry["totals"],
            }
        )
    by_project.sort(key=lambda row: (-row["totals"]["total_tokens"], str(row["name"])))

    by_source = [
        {"source": entry["key"] or usage_ledger.UNKNOWN_SOURCE, "totals": entry["totals"]}
        for entry in _group_totals(base, UsageRecord.source)
    ]
    by_source.sort(key=lambda row: (-row["totals"]["total_tokens"], row["source"]))

    member_query = base
    member_detail = "all"
    if role != "owner":
        member_query = member_query.filter(UsageRecord.user_id == user.id)
        member_detail = "self"

    members = {row.id: row for row in User.query.all()}
    roles = {
        row.user_id: row.role
        for row in WorkspaceMember.query.filter_by(workspace_id=workspace.id, status="active").all()
    }
    owner_id = workspace.user_id
    by_member = []
    for entry in _group_totals(member_query, UsageRecord.user_id):
        user_id = entry["key"]
        member = members.get(user_id)
        by_member.append(
            {
                "user_id": user_id,
                "username": member.username if member is not None else "Unknown",
                "role": "owner" if user_id == owner_id else roles.get(user_id),
                "totals": entry["totals"],
            }
        )
    by_member.sort(key=lambda row: (-row["totals"]["total_tokens"], row["username"]))

    return {
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "role": role,
        "member_detail": member_detail,
        "totals": totals,
        "by_project": by_project,
        "by_source": by_source,
        "by_member": by_member,
        "cost_is_estimate": True,
    }
