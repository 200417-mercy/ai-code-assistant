"""add monthly user and workspace budgets

Revision ID: b7c8d9e0f1a2
Revises: f0a1b2c3d4e5
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("token_limit", sa.Integer(), nullable=True),
        sa.Column("cost_limit", sa.Float(), nullable=True),
        sa.Column("warn_threshold", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND workspace_id IS NULL) OR "
            "(user_id IS NULL AND workspace_id IS NOT NULL)",
            name="ck_budgets_exactly_one_scope",
        ),
        sa.CheckConstraint(
            "token_limit IS NOT NULL OR cost_limit IS NOT NULL",
            name="ck_budgets_has_limit",
        ),
        sa.CheckConstraint(
            "warn_threshold > 0 AND warn_threshold <= 1",
            name="ck_budgets_warn_threshold",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"], unique=False)
    op.create_index("ix_budgets_workspace_id", "budgets", ["workspace_id"], unique=False)
    op.create_index("ix_budgets_is_active", "budgets", ["is_active"], unique=False)
    op.create_index(
        "ix_budgets_scope_period",
        "budgets",
        ["user_id", "workspace_id", "period"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_budgets_scope_period", table_name="budgets")
    op.drop_index("ix_budgets_is_active", table_name="budgets")
    op.drop_index("ix_budgets_workspace_id", table_name="budgets")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")
