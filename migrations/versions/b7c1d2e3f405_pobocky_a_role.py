"""pobocky a role

Pridáva pobočky (branches), priradenie manažérov k pobočkám
(user_branches) a stĺpec branch_id do oddelení a zamestnancov.

Existujúce dáta sa presunú do predvolenej pobočky "Hlavná pobočka" a
a existujúci manažéri (ktorí mali plný prístup) sa povýšia na
adminov, aby o nič neprišli.

Názov oddelenia už nie je jedinečný globálne, ale len v rámci pobočky.

Revision ID: b7c1d2e3f405
Revises: aeca62578799
Create Date: 2026-10-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c1d2e3f405'
down_revision = 'aeca62578799'
branch_labels = None
depends_on = None

DEFAULT_BRANCH_NAME = "Hlavná pobočka"

# Pre SQLite: pomenovanie bezmenných UNIQUE obmedzení pri batch
# migrácii (pôvodné obmedzenie na departments.name je bez mena).
NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _is_sqlite():
    return op.get_bind().dialect.name == "sqlite"


def _department_name_unique_constraint_name():
    inspector = sa.inspect(op.get_bind())

    for constraint in inspector.get_unique_constraints("departments"):
        if constraint["column_names"] == ["name"]:
            return constraint["name"] or "uq_departments_name"

    return None


def upgrade():
    bind = op.get_bind()

    if _is_sqlite():
        # Batch migrácia v SQLite prebudováva tabuľky (vytvorí novú,
        # skopíruje dáta, starú zahodí). Pri zapnutých cudzích
        # kľúčoch by zahodenie "departments"/"employees" zlyhalo,
        # keďže sa na ne odkazujú smeny a pod. Preto ich na čas
        # migrácie vypíname (postup odporúčaný v dokumentácii SQLite).
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    op.create_table(
        'branches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('active', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    op.create_table(
        'user_branches',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['branch_id'], ['branches.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('user_id', 'branch_id'),
    )

    bind.execute(
        sa.text("INSERT INTO branches (name, active) VALUES (:name, 1)"),
        {"name": DEFAULT_BRANCH_NAME},
    )
    default_branch_id = bind.execute(
        sa.text("SELECT id FROM branches WHERE name = :name"),
        {"name": DEFAULT_BRANCH_NAME},
    ).scalar()

    # --- 1. fáza: nový stĺpec (zatiaľ nullable) ---------------------
    for table in ("departments", "employees"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column('branch_id', sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                f'fk_{table}_branch_id',
                'branches',
                ['branch_id'],
                ['id'],
                ondelete='RESTRICT',
            )

        bind.execute(
            sa.text(f"UPDATE {table} SET branch_id = :branch_id"),
            {"branch_id": default_branch_id},
        )

    # --- 2. fáza: stĺpec povinný + zmena jedinečnosti názvu ---------
    old_unique_name = _department_name_unique_constraint_name()

    with op.batch_alter_table(
        "departments",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.alter_column(
            'branch_id', existing_type=sa.Integer(), nullable=False
        )

        if old_unique_name:
            batch_op.drop_constraint(old_unique_name, type_='unique')

        batch_op.create_unique_constraint(
            'uq_department_branch_name', ['branch_id', 'name']
        )

    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.alter_column(
            'branch_id', existing_type=sa.Integer(), nullable=False
        )

    # --- role: pôvodní manažéri mali plný prístup -> admin ----------
    bind.execute(
        sa.text("UPDATE users SET role = 'admin' WHERE role = 'manager'")
    )

    if _is_sqlite():
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()

    if _is_sqlite():
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    # Admin a manažér sa vrátia na pôvodnú jedinú rolu "manager".
    bind.execute(
        sa.text("UPDATE users SET role = 'manager' WHERE role = 'admin'")
    )

    with op.batch_alter_table("employees", schema=None) as batch_op:
        batch_op.drop_constraint('fk_employees_branch_id', type_='foreignkey')
        batch_op.drop_column('branch_id')

    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.drop_constraint('uq_department_branch_name', type_='unique')
        batch_op.drop_constraint(
            'fk_departments_branch_id', type_='foreignkey'
        )
        batch_op.drop_column('branch_id')
        batch_op.create_unique_constraint('uq_departments_name', ['name'])

    op.drop_table('user_branches')
    op.drop_table('branches')

    if _is_sqlite():
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
