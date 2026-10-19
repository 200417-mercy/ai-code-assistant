"""Per-workspace model price overrides (Phase 10, issue #442).

A workspace may negotiate or estimate different rates from the global default
price table. Each row overrides the input and output rate for one
``provider``/``model`` pair inside a single workspace.

Rows are owner-scoped: they belong to exactly one workspace, are only written
through the owner-only pricing API, and ``owner_id`` records the workspace
owner that set the override for audit purposes.
"""

from datetime import UTC, datetime

from app.extensions import db


class WorkspacePriceOverride(db.Model):
    """One provider/model price override for a workspace."""

    __tablename__ = "workspace_price_overrides"

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(
        db.Integer,
        db.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    input_per_1k_usd = db.Column(db.Float, nullable=False, default=0.0)
    output_per_1k_usd = db.Column(db.Float, nullable=False, default=0.0)
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

    workspace = db.relationship("Workspace", foreign_keys=[workspace_id], lazy="select")
    owner = db.relationship("User", foreign_keys=[owner_id], lazy="select")

    __table_args__ = (
        db.UniqueConstraint(
            "workspace_id",
            "provider",
            "model",
            name="uq_workspace_price_override",
        ),
    )

    def to_dict(self) -> dict:
        """Serialize the override for JSON API responses."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "owner_id": self.owner_id,
            "provider": self.provider,
            "model": self.model,
            "input_per_1k_usd": self.input_per_1k_usd,
            "output_per_1k_usd": self.output_per_1k_usd,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WorkspacePriceOverride workspace={self.workspace_id} "
            f"{self.provider}/{self.model}>"
        )


#: Shorter alias used by callers that scope the model by the import path.
PriceOverride = WorkspacePriceOverride
