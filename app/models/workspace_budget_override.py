"""Time-bounded workspace budget overrides (Phase 10, issue #467).

An override raises a workspace's spending budget for an explicit window. Rows
are owner-scoped, carry the reason for the increase, and record the user that
created them. The effective budget falls back to the caller's default once the
window closes.
"""

from datetime import UTC, datetime

from app.extensions import db


class WorkspaceBudgetOverride(db.Model):
    """One temporary budget increase for a workspace."""

    __tablename__ = "workspace_budget_overrides"

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(
        db.Integer,
        db.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    limit_amount = db.Column(db.Float, nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    workspace = db.relationship("Workspace", foreign_keys=[workspace_id], lazy="select")
    created_by = db.relationship("User", foreign_keys=[created_by_id], lazy="select")

    __table_args__ = (
        db.CheckConstraint("limit_amount > 0", name="ck_budget_override_positive_limit"),
        db.CheckConstraint("expires_at > starts_at", name="ck_budget_override_valid_window"),
    )

    def to_dict(self) -> dict:
        """Serialize the override for JSON API responses."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "created_by_id": self.created_by_id,
            "limit_amount": self.limit_amount,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WorkspaceBudgetOverride workspace={self.workspace_id} " f"limit={self.limit_amount}>"
        )
