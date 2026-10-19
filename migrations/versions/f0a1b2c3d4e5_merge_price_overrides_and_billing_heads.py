"""merge workspace price overrides and provider billing records heads

Revision ID: f0a1b2c3d4e5
Revises: 0c1d2e3f4a5b, a9f8e7d6c5b4
Create Date: 2026-10-19 11:00:00.000000

The Phase 10 workspace price overrides branch (``0c1d2e3f4a5b``) and the
provider billing records branch (``a9f8e7d6c5b4``) diverged from a common
ancestor. This empty merge revision unifies them into a single head so
``flask db upgrade`` and the migration tests have one target.
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision = "f0a1b2c3d4e5"
down_revision = ("0c1d2e3f4a5b", "a9f8e7d6c5b4")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
