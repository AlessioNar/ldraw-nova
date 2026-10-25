"""phase 2: build_draft.extra, proposal.chat_id without a foreign key

`build_draft.extra` holds the draft.json keys that have no column of their own (forked, drift, sources, ...), so the
draft record round-trips exactly. `proposal.chat_id` loses its foreign key: a proposal file kept the id of the chat
that made it after the chat was deleted, and the database now does the same. No data is rewritten.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 09:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('build_draft', sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()),
                                           server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.drop_constraint('proposal_chat_id_fkey', 'proposal', type_='foreignkey')


def downgrade() -> None:
    op.execute("UPDATE proposal SET chat_id = NULL WHERE chat_id IS NOT NULL AND chat_id NOT IN (SELECT id FROM chat)")
    op.create_foreign_key('proposal_chat_id_fkey', 'proposal', 'chat', ['chat_id'], ['id'], ondelete='SET NULL')
    op.drop_column('build_draft', 'extra')
