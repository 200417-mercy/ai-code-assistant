"""add cost recomputation timestamp to usage records

Revision ID: c7d8e9f0a1b2
Revises: b7c8d9e0f1a2
Create Date: 2026-10-21 00:00:00.000000

Phase 10: record when a historical usage row's cost estimate was recomputed
after a price-table change (issue #440). Nullable so existing rows stay valid
until the recompute command updates them.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.add_column(
            sa.Column("cost_recomputed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("usage_records") as batch_op:
        batch_op.drop_column("cost_recomputed_at")

