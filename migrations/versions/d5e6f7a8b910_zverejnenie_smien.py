"""zverejnenie smien

Pridáva do smien príznak ``published`` (0 = koncept, 1 = zverejnená).
Všetky doterajšie smeny sa označia ako zverejnené, aby zamestnanci po
aktualizácii o svoje smeny neprišli.

Revision ID: d5e6f7a8b910
Revises: c4d5e6f7a809
Create Date: 2026-10-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b910'
down_revision = 'c4d5e6f7a809'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shifts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'published',
                sa.Integer(),
                nullable=False,
                server_default='0',
            )
        )

    # Doterajšie smeny už zamestnanci vidia - ostávajú zverejnené.
    op.execute(sa.text("UPDATE shifts SET published = 1"))


def downgrade():
    with op.batch_alter_table("shifts", schema=None) as batch_op:
        batch_op.drop_column('published')
