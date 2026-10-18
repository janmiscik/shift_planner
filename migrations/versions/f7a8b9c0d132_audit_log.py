"""audit log

Pridáva tabuľku audit_log - záznamy o zmenách (kto, kedy, čo).

Revision ID: f7a8b9c0d132
Revises: e6f7a8b9c021
Create Date: 2026-10-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a8b9c0d132'
down_revision = 'e6f7a8b9c021'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('entity_type', sa.String(), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('branch_id', sa.Integer(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ['branch_id'], ['branches.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])
    op.create_index('ix_audit_log_branch_id', 'audit_log', ['branch_id'])
    op.create_index('ix_audit_log_entity_type', 'audit_log', ['entity_type'])


def downgrade():
    op.drop_index('ix_audit_log_entity_type', table_name='audit_log')
    op.drop_index('ix_audit_log_branch_id', table_name='audit_log')
    op.drop_index('ix_audit_log_created_at', table_name='audit_log')
    op.drop_table('audit_log')
