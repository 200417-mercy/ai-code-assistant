"""add per-workspace price overrides

Revision ID: 0c1d2e3f4a5b
Revises: f9e8d7c6b5a4
Create Date: 2026-10-17 00:00:00.000000

Phase 10: allow a workspace to override the global price table for specific
provider/model pairs (issue #442). Rows are removed with their workspace, and a
unique constraint keeps one override per workspace/provider/model.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0c1d2e3f4a5b"
down_revision = "f9e8d7c6b5a4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_price_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_per_1k_usd", sa.Float(), nullable=False),
        sa.Column("output_per_1k_usd", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "model",
            name="uq_workspace_price_override",
        ),
    )
    op.create_index(
        "ix_workspace_price_overrides_workspace_id",
        "workspace_price_overrides",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_price_overrides_owner_id",
        "workspace_price_overrides",
        ["owner_id"],
    )


def downgrade():
    op.drop_index(
        "ix_workspace_price_overrides_owner_id",
        table_name="workspace_price_overrides",
    )
    op.drop_index(
        "ix_workspace_price_overrides_workspace_id",
        table_name="workspace_price_overrides",
    )
    op.drop_table("workspace_price_overrides")
