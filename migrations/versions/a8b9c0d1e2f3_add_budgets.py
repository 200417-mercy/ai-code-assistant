"""add per-user and per-workspace budgets

Revision ID: a8b9c0d1e2f3
Revises: f0a1b2c3d4e5
"""

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("token_limit", sa.Integer(), nullable=True),
        sa.Column("cost_limit", sa.Float(), nullable=True),
        sa.Column("period", sa.String(length=20), nullable=False, server_default="monthly"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND workspace_id IS NULL) OR "
            "(user_id IS NULL AND workspace_id IS NOT NULL)",
            name="ck_budget_exactly_one_scope",
        ),
        sa.UniqueConstraint("user_id", "period", name="uq_budget_user_period"),
        sa.UniqueConstraint("workspace_id", "period", name="uq_budget_workspace_period"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
    op.create_index("ix_budgets_workspace_id", "budgets", ["workspace_id"])


def downgrade():
    op.drop_index("ix_budgets_workspace_id", table_name="budgets")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")
