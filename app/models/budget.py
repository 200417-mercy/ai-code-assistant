"""Spending budgets (Phase 10, issues #461 and #468).

A budget belongs to exactly one owner. Two scopes are supported:

* **user budget** — ``workspace_id`` is null; only the owning user may read or
  modify it, even if another account is a site administrator;
* **workspace budget** — ``workspace_id`` is set; the workspace owner and site
  administrators may read or modify it.

Amounts are stored in the configured display currency (three-letter code). The
model deliberately stores no derived spend: dashboards compute spend from the
append-only :class:`~app.models.usage_record.UsageRecord` ledger so a budget can
never fabricate usage.
"""

from datetime import UTC, datetime

from app.extensions import db

#: Periods understood by budget validation and dashboards.
BUDGET_PERIODS = ("daily", "weekly", "monthly", "yearly")


class Budget(db.Model):
    """A user- or workspace-scoped spending limit."""

    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id = db.Column(
        db.Integer,
        db.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    limit_amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    period = db.Column(db.String(20), nullable=False, default="monthly")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    owner = db.relationship("User", foreign_keys=[user_id], lazy="select")
    workspace = db.relationship("Workspace", foreign_keys=[workspace_id], lazy="select")

    @property
    def scope(self) -> str:
        """Return ``"workspace"`` or ``"user"``."""
        return "workspace" if self.workspace_id is not None else "user"

    def to_dict(self) -> dict:
        """Serialize the budget for JSON API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "scope": self.scope,
            "name": self.name,
            "limit_amount": self.limit_amount,
            "currency": self.currency,
            "period": self.period,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        scope = f"workspace={self.workspace_id}" if self.workspace_id else f"user={self.user_id}"
        return f"<Budget id={self.id} {scope} {self.period} {self.limit_amount}>"
