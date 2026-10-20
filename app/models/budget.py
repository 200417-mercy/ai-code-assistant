"""Per-user and per-workspace budgets (Phase 10)."""

from datetime import UTC, datetime

from app.extensions import db


class Budget(db.Model):
    """A token/cost budget for exactly one user or workspace."""

    __tablename__ = "budgets"
    __table_args__ = (
        db.CheckConstraint(
            "(user_id IS NOT NULL AND workspace_id IS NULL) OR "
            "(user_id IS NULL AND workspace_id IS NOT NULL)",
            name="ck_budget_exactly_one_scope",
        ),
        db.UniqueConstraint("user_id", "period", name="uq_budget_user_period"),
        db.UniqueConstraint("workspace_id", "period", name="uq_budget_workspace_period"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    workspace_id = db.Column(
        db.Integer,
        db.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    token_limit = db.Column(db.Integer, nullable=True)
    cost_limit = db.Column(db.Float, nullable=True)
    period = db.Column(db.String(20), nullable=False, default="monthly")
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user = db.relationship("User", backref=db.backref("budgets", cascade="all, delete-orphan"))
    workspace = db.relationship(
        "Workspace", backref=db.backref("budgets", cascade="all, delete-orphan")
    )

    def is_user_scoped(self) -> bool:
        return self.user_id is not None and self.workspace_id is None

    def is_workspace_scoped(self) -> bool:
        return self.workspace_id is not None and self.user_id is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "token_limit": self.token_limit,
            "cost_limit": self.cost_limit,
            "period": self.period,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
