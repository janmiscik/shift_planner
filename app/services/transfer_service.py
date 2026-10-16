"""Presun dát zo SQLite súboru do inej databázy (PostgreSQL).

Používa sa raz, pri prechode z lokálnej SQLite databázy na PostgreSQL
(centrálna inštancia pre viac pobočiek):

  1. ``python manage.py migrate``  (nad SQLite - dostane schému na
     najnovšiu verziu),
  2. v ``.env`` nastav ``DATABASE_URL`` na PostgreSQL a znova
     ``python manage.py migrate`` (vytvorí prázdnu schému),
  3. ``python manage.py import-sqlite`` (skopíruje dáta).

Kopíruje sa v jednej transakcii - pri akejkoľvek chybe sa cieľová
databáza nezmení. Cieľ musí byť prázdny, aby sa nič neprepísalo. Jediná
výnimka: predvolená "Hlavná pobočka", ktorú vytvorí samotná migrácia
v čerstvej databáze - tá sa pri importe nahradí pobočkami zo SQLite.
"""

import sqlalchemy as sa

from app.extensions import db
from app.services.branch_service import DEFAULT_BRANCH_NAME


class TransferError(RuntimeError):
    """Presun dát sa nedal vykonať (chyba je popísaná v správe)."""


def _alembic_version(connection):
    inspector = sa.inspect(connection)

    if "alembic_version" not in inspector.get_table_names():
        return None

    return connection.execute(
        sa.text("SELECT version_num FROM alembic_version")
    ).scalar()


def copy_sqlite_to_database(sqlite_path, target_engine, chunk_size=1000):
    """Skopíruje všetky tabuľky aplikácie zo SQLite súboru do cieľovej
    databázy. Vráti slovník {tabuľka: počet skopírovaných riadkov}.

    Vyhodí ``TransferError``, ak: zdrojový súbor chýba, schémy majú
    rozdielnu verziu (treba spustiť ``migrate``), cieľ nie je prázdny,
    alebo dáta nemožno do cieľa vložiť.
    """

    source_engine = sa.create_engine(f"sqlite:///{sqlite_path}")

    try:
        with source_engine.connect() as source:
            source_tables = set(sa.inspect(source).get_table_names())

            if "employees" not in source_tables:
                raise TransferError(
                    f"Súbor '{sqlite_path}' nevyzerá ako databáza appky "
                    "(chýba tabuľka employees)."
                )

            with target_engine.connect() as target_check:
                source_version = _alembic_version(source)
                target_version = _alembic_version(target_check)

                if target_version is None:
                    raise TransferError(
                        "Cieľová databáza nemá schému. Najprv spusti: "
                        "python manage.py migrate"
                    )

                if source_version != target_version:
                    raise TransferError(
                        f"Verzia schémy sa líši (SQLite: {source_version}, "
                        f"cieľ: {target_version}). Najprv spusti "
                        "'python manage.py migrate' nad SQLite súborom "
                        "(bez DATABASE_URL), potom to skús znova."
                    )

                replace_default_branch = False

                for table in db.metadata.sorted_tables:
                    count = target_check.execute(
                        sa.select(sa.func.count()).select_from(table)
                    ).scalar()

                    if table.name == "branches" and count == 1:
                        only_branch = target_check.execute(
                            sa.select(table.c.name)
                        ).scalar()

                        if only_branch == DEFAULT_BRANCH_NAME:
                            # Čerstvo vytvorená predvolená pobočka z
                            # migrácie - nie sú to používateľské dáta.
                            replace_default_branch = True
                            continue

                    if count:
                        raise TransferError(
                            f"Cieľová databáza nie je prázdna (tabuľka "
                            f"'{table.name}' má {count} riadkov). Z "
                            "bezpečnostných dôvodov sa nič neprepisuje."
                        )

            copied = {}

            try:
                with target_engine.begin() as target:
                    if replace_default_branch:
                        target.execute(
                            db.metadata.tables["branches"].delete()
                        )

                    for table in db.metadata.sorted_tables:
                        rows = [
                            dict(row)
                            for row in source.execute(
                                sa.select(table)
                            ).mappings()
                        ]

                        for start in range(0, len(rows), chunk_size):
                            target.execute(
                                table.insert(), rows[start:start + chunk_size]
                            )

                        copied[table.name] = len(rows)

                    if target.dialect.name == "postgresql":
                        _reset_postgres_sequences(target)

                    for table in db.metadata.sorted_tables:
                        count = target.execute(
                            sa.select(sa.func.count()).select_from(table)
                        ).scalar()

                        if count != copied[table.name]:
                            raise TransferError(
                                f"Kontrola zlyhala pri tabuľke "
                                f"'{table.name}': skopírovaných "
                                f"{copied[table.name]}, v cieli {count}."
                            )
            except TransferError:
                raise
            except sa.exc.SQLAlchemyError as error:
                raise TransferError(
                    "Dáta sa nepodarilo vložiť do cieľovej databázy "
                    f"(nič sa nezmenilo): {error.__cause__ or error}"
                ) from error

            return copied
    finally:
        source_engine.dispose()


def _reset_postgres_sequences(connection):
    """Po vložení riadkov s pevnými ID treba posunúť PostgreSQL
    sekvencie, inak by ďalší nový záznam dostal už obsadené ID."""

    for table in db.metadata.sorted_tables:
        if "id" not in table.c or not table.c.id.primary_key:
            continue

        connection.execute(
            sa.text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table.name}"
            )
        )
