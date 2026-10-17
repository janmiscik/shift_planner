"""ziadosti o volno

Pridáva tabuľku leave_requests - žiadosti zamestnancov o voľno, ktoré
schvaľuje manažér.

Revision ID: e6f7a8b9c021
Revises: d5e6f7a8b910
Create Date: 2026-10-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6f7a8b9c021'
down_revision = 'd5e6f7a8b910'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'leave_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('absence_type', sa.String(), nullable=False),
        sa.Column('start_date', sa.String(), nullable=False),
        sa.Column('end_date', sa.String(), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column(
            'status',
            sa.String(),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('decided_at', sa.String(), nullable=True),
        sa.Column('decided_by_user_id', sa.Integer(), nullable=True),
        sa.Column('decision_note', sa.String(), nullable=True),
        sa.Column('absence_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['absence_id'], ['absences.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['decided_by_user_id'], ['users.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['employees.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('leave_requests')
