"""session ownership, job queue and run events

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 18:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

BIGPK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.add_column('documents', sa.Column('session_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_documents_session_hash', 'documents', ['session_hash'], unique=False)
    op.add_column('runs', sa.Column('session_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_runs_session_hash', 'runs', ['session_hash'], unique=False)
    op.create_table('jobs',
    sa.Column('id', BIGPK, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id')
    )
    op.create_index('ix_jobs_status_id', 'jobs', ['status', 'id'], unique=False)
    op.create_table('run_events',
    sa.Column('id', BIGPK, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('data', sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'seq')
    )
    op.create_index('ix_run_events_run_id', 'run_events', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_run_events_run_id', table_name='run_events')
    op.drop_table('run_events')
    op.drop_index('ix_jobs_status_id', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index('ix_runs_session_hash', table_name='runs')
    op.drop_column('runs', 'session_hash')
    op.drop_index('ix_documents_session_hash', table_name='documents')
    op.drop_column('documents', 'session_hash')
