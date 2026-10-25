"""phase 2b: the parts collection on PostgreSQL

The reference schema's collection tables could not hold what the JSON files hold, so they are completed:

* `part_source.csv_text` (the original upload) and `part_source.extra` (top-level keys with no column), so a source
  round-trips exactly and the derived JSON export for the toolkit CLI equals the file it replaces.
* `part_row.pos`/`extra` and `part_review.pos`/`extra`: the row order and any key a row carries beyond the columns.
* `part_change` (uuid batches, a fixed list of ops, columns per field) becomes `part_log`: one row per log entry with the
  entry as jsonb and an integer batch, which is what undo and history read. The old table was never written.
* `part_note_log` (the notes' change log) and `part_reservation` (parts held by models the owner built).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 14:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column('part_source', sa.Column('csv_text', sa.Text(), nullable=True))
    op.add_column('part_source', sa.Column('extra', JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('part_row', sa.Column('pos', sa.Integer(), server_default='0', nullable=False))
    op.add_column('part_row', sa.Column('extra', JSONB, nullable=True))
    op.add_column('part_review', sa.Column('pos', sa.Integer(), server_default='0', nullable=False))
    op.add_column('part_review', sa.Column('extra', JSONB, nullable=True))
    op.drop_table('part_change')
    op.create_table('part_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('batch', sa.Integer(), nullable=False),
    sa.Column('entry', JSONB, nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['part_source.id'], name=op.f('part_log_source_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('part_log_pkey'))
    )
    op.create_index('part_log_source_batch', 'part_log', ['source_id', 'batch'], unique=False)
    op.create_table('part_note_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('entry', JSONB, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('part_note_log_pkey'))
    )
    op.create_table('part_reservation',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('body', JSONB, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('part_reservation_pkey'))
    )


def downgrade() -> None:
    op.drop_table('part_reservation')
    op.drop_table('part_note_log')
    op.drop_index('part_log_source_batch', table_name='part_log')
    op.drop_table('part_log')
    op.create_table('part_change',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('batch', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('ts', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('op', sa.Text(), nullable=False),
    sa.Column('part', sa.Text(), nullable=True),
    sa.Column('colour', sa.Integer(), nullable=True),
    sa.Column('delta', sa.Integer(), nullable=True),
    sa.Column('qty_before', sa.Integer(), nullable=True),
    sa.Column('qty_after', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint("op IN ('add','remove','set','import','replace','resolve','undo')", name=op.f('part_change_op_check')),
    sa.ForeignKeyConstraint(['source_id'], ['part_source.id'], name=op.f('part_change_source_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('part_change_pkey'))
    )
    op.create_index('part_change_source_batch', 'part_change', ['source_id', 'batch'], unique=False)
    op.create_index('part_change_part_idx', 'part_change', ['source_id', 'part', 'colour'], unique=False)
    op.drop_column('part_review', 'extra')
    op.drop_column('part_review', 'pos')
    op.drop_column('part_row', 'extra')
    op.drop_column('part_row', 'pos')
    op.drop_column('part_source', 'extra')
    op.drop_column('part_source', 'csv_text')
