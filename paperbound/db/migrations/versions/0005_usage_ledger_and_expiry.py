"""usage ledger (rate limit and LLM budget) and document expiry

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22 15:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('usage_ledger',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=True),
    sa.Column('usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_ledger_kind_created_at', 'usage_ledger', ['kind', 'created_at'], unique=False)
    op.add_column('documents', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'expires_at')
    op.drop_index('ix_usage_ledger_kind_created_at', table_name='usage_ledger')
    op.drop_table('usage_ledger')
