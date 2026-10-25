"""dostupnost zamestnancov

Pridáva tabuľku availability_rules - kedy zamestnanec nemôže pracovať
(opakovane alebo jednorazovo).

Revision ID: c0d1e2f3a465
Revises: b9c0d1e2f354
Create Date: 2026-10-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c0d1e2f3a465'
down_revision = 'b9c0d1e2f354'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'availability_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('weekday', sa.Integer(), nullable=True),
        sa.Column('date_from', sa.String(), nullable=True),
        sa.Column('date_to', sa.String(), nullable=True),
        sa.Column('time_from', sa.String(), nullable=True),
        sa.Column('time_to', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['employees.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('availability_rules')
