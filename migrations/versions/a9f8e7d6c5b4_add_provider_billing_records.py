"""add provider billing records

Revision ID: a9f8e7d6c5b4
Revises: e7a8b9c0d1e2
Create Date: 2026-10-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9f8e7d6c5b4'
down_revision = 'e7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'provider_billing_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('amount_usd', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('source_filename', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_provider_billing_records_user_id',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'provider',
            'period',
            name='uq_provider_billing_user_provider_period',
        ),
    )
    with op.batch_alter_table('provider_billing_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_provider_billing_records_user_id'), ['user_id'])
        batch_op.create_index(batch_op.f('ix_provider_billing_records_provider'), ['provider'])
        batch_op.create_index(batch_op.f('ix_provider_billing_records_period'), ['period'])


def downgrade():
    with op.batch_alter_table('provider_billing_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_billing_records_period'))
        batch_op.drop_index(batch_op.f('ix_provider_billing_records_provider'))
        batch_op.drop_index(batch_op.f('ix_provider_billing_records_user_id'))
    op.drop_table('provider_billing_records')
