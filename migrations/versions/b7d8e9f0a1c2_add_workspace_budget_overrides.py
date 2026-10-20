"""add workspace budget overrides

Revision ID: b7d8e9f0a1c2
Revises: f0a1b2c3d4e5
Create Date: 2026-10-20 00:00:00.000000

Phase 10: allow a workspace owner to raise the spending budget for an explicit
window (issue #467). Rows are removed with their workspace, the creator is kept
as nullable attribution, and both the positive limit and the ordered window are
enforced in the database.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7d8e9f0a1c2"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_budget_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("limit_amount", sa.Float(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
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
        sa.CheckConstraint("limit_amount > 0", name="ck_budget_override_positive_limit"),
        sa.CheckConstraint("expires_at > starts_at", name="ck_budget_override_valid_window"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_budget_overrides_workspace_id",
        "workspace_budget_overrides",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_budget_overrides_created_by_id",
        "workspace_budget_overrides",
        ["created_by_id"],
    )


def downgrade():
    op.drop_index(
        "ix_workspace_budget_overrides_created_by_id",
        table_name="workspace_budget_overrides",
    )
    op.drop_index(
        "ix_workspace_budget_overrides_workspace_id",
        table_name="workspace_budget_overrides",
    )
    op.drop_table("workspace_budget_overrides")
