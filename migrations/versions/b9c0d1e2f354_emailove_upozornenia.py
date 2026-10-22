"""emailove upozornenia

Pridáva e-mailovú adresu a súhlas s upozorneniami do účtov (users) a
frontu e-mailov na odoslanie (email_outbox).

Revision ID: b9c0d1e2f354
Revises: a8b9c0d1e243
Create Date: 2026-10-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9c0d1e2f354'
down_revision = 'a8b9c0d1e243'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'notify_email',
                sa.Integer(),
                nullable=False,
                server_default='1',
            )
        )

    op.create_table(
        'email_outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recipient', sa.String(), nullable=False),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column(
            'status', sa.String(), nullable=False, server_default='pending'
        ),
        sa.Column(
            'attempts', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.Column('next_attempt_at', sa.String(), nullable=True),
        sa.Column('sent_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_email_outbox_status', 'email_outbox', ['status'])


def downgrade():
    op.drop_index('ix_email_outbox_status', table_name='email_outbox')
    op.drop_table('email_outbox')

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column('notify_email')
        batch_op.drop_column('email')
