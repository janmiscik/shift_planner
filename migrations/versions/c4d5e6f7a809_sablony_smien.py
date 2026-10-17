"""sablony smien

Pridáva šablóny týždňa smien (shift_templates a
shift_template_entries) pre kopírovanie/aplikovanie rozpisu na ďalšie
týždne.

Revision ID: c4d5e6f7a809
Revises: b7c1d2e3f405
Create Date: 2026-10-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a809'
down_revision = 'b7c1d2e3f405'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'shift_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ['branch_id'], ['branches.id'], ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'branch_id', 'name', name='uq_shift_template_branch_name'
        ),
    )

    op.create_table(
        'shift_template_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.String(), nullable=False),
        sa.Column('end_time', sa.String(), nullable=False),
        sa.Column('shift_type', sa.String(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['department_id'], ['departments.id'], ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['employees.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['template_id'], ['shift_templates.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('shift_template_entries')
    op.drop_table('shift_templates')
