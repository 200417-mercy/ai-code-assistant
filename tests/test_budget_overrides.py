"""Tests for time-bounded workspace budget overrides (issue #467)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import AuditLog, Workspace
from app.services import audit
from app.services import budget_overrides as overrides

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _workspace(db, make_user):
    user = make_user()
    workspace = Workspace(user_id=user.id, name="Budget workspace")
    db.session.add(workspace)
    db.session.commit()
    return user, workspace


def _create(workspace, user, *, starts_at, expires_at, limit=500.0, reason="Launch week"):
    return overrides.create_budget_override(
        workspace,
        limit=limit,
        starts_at=starts_at,
        expires_at=expires_at,
        reason=reason,
        user=user,
    )


def test_active_override_raises_the_effective_limit(db, make_user):
    user, workspace = _workspace(db, make_user)
    _create(
        workspace,
        user,
        starts_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
    )

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 500.0


def test_override_does_not_apply_before_its_window(db, make_user):
    user, workspace = _workspace(db, make_user)
    _create(
        workspace,
        user,
        starts_at=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(hours=2),
    )

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 100


def test_override_does_not_apply_after_its_window(db, make_user):
    user, workspace = _workspace(db, make_user)
    _create(
        workspace,
        user,
        starts_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 100


def test_missing_override_returns_the_default(db, make_user):
    _user, workspace = _workspace(db, make_user)

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 100
    assert overrides.effective_budget_limit(workspace, default=None, at=NOW) is None


def test_boundary_is_inclusive_at_start_and_exclusive_at_expiry(db, make_user):
    user, workspace = _workspace(db, make_user)
    _create(
        workspace,
        user,
        starts_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 500.0
    assert (
        overrides.effective_budget_limit(workspace, default=100, at=NOW + timedelta(hours=1)) == 100
    )


def test_newer_active_override_wins(db, make_user):
    user, workspace = _workspace(db, make_user)
    _create(
        workspace,
        user,
        starts_at=NOW - timedelta(hours=2),
        expires_at=NOW + timedelta(hours=2),
        limit=300,
    )
    _create(
        workspace,
        user,
        starts_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        limit=700,
    )

    assert overrides.effective_budget_limit(workspace, default=100, at=NOW) == 700


def test_creation_records_who_set_the_override_and_why(db, make_user):
    user, workspace = _workspace(db, make_user)
    row = _create(
        workspace,
        user,
        starts_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        reason="Temporary marketing push",
    )

    entry = AuditLog.query.filter_by(action=audit.BUDGET_OVERRIDE_CREATED).one()
    assert entry.user_id == user.id
    assert entry.target_type == "workspace"
    assert entry.target_id == workspace.id
    assert entry.metadata_dict == {
        "override_id": row.id,
        "limit_amount": 500.0,
        "starts_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=2)).isoformat(),
        "reason": "Temporary marketing push",
        "diff": {"limit_amount": {"before": None, "after": 500.0}},
    }


@pytest.mark.parametrize(
    "limit, starts_at, expires_at, reason",
    [
        (0, NOW, NOW + timedelta(hours=1), "Reason"),
        (-1, NOW, NOW + timedelta(hours=1), "Reason"),
        (100, NOW, NOW, "Reason"),
        (100, NOW + timedelta(hours=1), NOW, "Reason"),
        (100, NOW, NOW + timedelta(hours=1), "   "),
    ],
)
def test_invalid_override_inputs_are_rejected(db, make_user, limit, starts_at, expires_at, reason):
    user, workspace = _workspace(db, make_user)

    with pytest.raises(overrides.BudgetOverrideError):
        overrides.create_budget_override(
            workspace,
            limit=limit,
            starts_at=starts_at,
            expires_at=expires_at,
            reason=reason,
            user=user,
        )


def test_non_owner_cannot_create_an_override(db, make_user):
    _owner, workspace = _workspace(db, make_user)
    other = make_user(username="other", email="other@example.com")

    with pytest.raises(overrides.BudgetOverrideError):
        overrides.create_budget_override(
            workspace,
            limit=500,
            starts_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            reason="Not mine",
            user=other,
        )
