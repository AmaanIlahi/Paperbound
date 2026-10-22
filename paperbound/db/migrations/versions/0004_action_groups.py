"""action groups: group_key and group_mode on proposed_actions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('proposed_actions', sa.Column('group_key', sa.String(), nullable=True))
    op.add_column('proposed_actions', sa.Column('group_mode', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('proposed_actions', 'group_mode')
    op.drop_column('proposed_actions', 'group_key')
