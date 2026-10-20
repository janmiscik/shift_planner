"""vymena smien

Pridáva tabuľku shift_swaps - žiadosti o výmenu alebo odovzdanie smeny
medzi kolegami, ktoré schvaľuje manažér.

Revision ID: a8b9c0d1e243
Revises: f7a8b9c0d132
Create Date: 2026-10-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8b9c0d1e243'
down_revision = 'f7a8b9c0d132'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'shift_swaps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proposer_employee_id', sa.Integer(), nullable=False),
        sa.Column('colleague_employee_id', sa.Integer(), nullable=False),
        sa.Column('offered_shift_id', sa.Integer(), nullable=True),
        sa.Column('counter_shift_id', sa.Integer(), nullable=True),
        sa.Column(
            'wants_swap', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column('offered_text', sa.String(), nullable=False),
        sa.Column('counter_text', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column(
            'status',
            sa.String(),
            nullable=False,
            server_default='pending_colleague',
        ),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('colleague_responded_at', sa.String(), nullable=True),
        sa.Column('decided_at', sa.String(), nullable=True),
        sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
        sa.Column('decision_note', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ['colleague_employee_id'], ['employees.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['counter_shift_id'], ['shifts.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['decided_by_user_id'], ['users.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['offered_shift_id'], ['shifts.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['proposer_employee_id'], ['employees.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('shift_swaps')
