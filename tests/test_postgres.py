"""Testy špecifické pre PostgreSQL (zálohy cez pg_dump, migrácia).

Spúšťajú sa len ak je nastavené ``TEST_DATABASE_URL`` (pozri
``tests/conftest.py``) a v PATH je ``pg_dump``.
"""

import os
import re
import shutil
import subprocess

import pytest

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL nie je nastavené",
    ),
    pytest.mark.skipif(
        shutil.which("pg_dump") is None, reason="pg_dump nie je v PATH"
    ),
]


@pytest.fixture
def pg_app():
    from app.extensions import db
    from app.web.app import create_app

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    with app.app_context():
        db.create_all()

    return app


def test_postgres_backup_is_valid_dump(pg_app, tmp_path):
    from app.services.backup_service import create_postgres_backup

    uri = pg_app.config["SQLALCHEMY_DATABASE_URI"]
    backup = create_postgres_backup(uri, label="test", backups_dir=tmp_path)

    assert backup.exists() and backup.suffix == ".dump"
    assert backup.read_bytes().startswith(b"PGDMP")

    if shutil.which("pg_restore"):
        listing = subprocess.run(
            ["pg_restore", "--list", str(backup)],
            capture_output=True,
            text=True,
        )
        assert listing.returncode == 0
        assert "branches" in listing.stdout


def test_postgres_backup_error_is_reported(pg_app, tmp_path):
    from app.services.backup_service import BackupError, create_postgres_backup

    with pytest.raises(BackupError):
        create_postgres_backup(
            "postgresql+psycopg2://planner:zleheslo@127.0.0.1:1/neexistuje",
            backups_dir=tmp_path,
        )

    assert list(tmp_path.glob("*.dump")) == []


def test_admin_backup_route_downloads_postgres_dump(pg_app):
    from app.services.auth_service import create_admin

    with pg_app.app_context():
        create_admin("admin", "adminpass1")

    client = pg_app.test_client()
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    client.post(
        "/login",
        data={"csrf_token": token, "username": "admin", "password": "adminpass1"},
    )

    response = client.get("/admin/backup")

    assert response.status_code == 200
    assert ".dump" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"PGDMP")


def test_migrate_makes_backup_first_then_upgrades(
    pg_app, tmp_path, monkeypatch
):
    import manage
    import sqlalchemy as sa
    from flask_migrate import upgrade

    import app.services.backup_service as backup_service
    import app.web.app as app_module
    from app.extensions import db

    # Databáza vo verzii pred pobočkami (už s tabuľkami).
    with pg_app.app_context():
        db.drop_all()
        db.session.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()
        upgrade(revision="aeca62578799")

    monkeypatch.setattr(backup_service, "default_backups_dir", lambda: tmp_path)
    monkeypatch.setattr(app_module, "create_app", app_module._create_app_keep_data)
    monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])

    manage._run_migrate_postgres(pg_app.config["SQLALCHEMY_DATABASE_URI"])

    assert len(list(tmp_path.glob("*pred-migraciou*.dump"))) == 1

    with pg_app.app_context():
        tables = set(sa.inspect(db.engine).get_table_names())
        assert "branches" in tables


def test_migrate_aborts_when_backup_is_not_possible(
    pg_app, tmp_path, monkeypatch
):
    import manage
    import sqlalchemy as sa
    from flask_migrate import upgrade

    import app.services.backup_service as backup_service
    import app.web.app as app_module
    from app.extensions import db

    with pg_app.app_context():
        db.drop_all()
        db.session.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()
        upgrade(revision="aeca62578799")

    monkeypatch.setattr(backup_service.shutil, "which", lambda name: None)
    monkeypatch.setattr(app_module, "create_app", app_module._create_app_keep_data)
    monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])

    with pytest.raises(SystemExit) as exit_info:
        manage._run_migrate_postgres(pg_app.config["SQLALCHEMY_DATABASE_URI"])

    assert exit_info.value.code == 1

    # Migrácia sa nespustila - schéma ostala vo verzii pred pobočkami.
    with pg_app.app_context():
        assert "branches" not in set(sa.inspect(db.engine).get_table_names())


# ---------------------------------------------------------------------
# Import dát zo SQLite do PostgreSQL
# ---------------------------------------------------------------------

def _fresh_migrated_postgres(pg_app):
    import sqlalchemy as sa
    from flask_migrate import upgrade

    from app.extensions import db

    with pg_app.app_context():
        db.drop_all()
        db.session.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()
        upgrade()

        return db.session.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar()


def _make_sqlite_source(path, version):
    import sqlalchemy as sa

    from app.extensions import db

    tables = db.metadata.tables
    engine = sa.create_engine(f"sqlite:///{path}")
    db.metadata.create_all(engine)

    with engine.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        c.execute(
            sa.text("INSERT INTO alembic_version VALUES (:v)"), {"v": version}
        )
        c.execute(tables["branches"].insert(), [
            {"id": 1, "name": "Hlavná pobočka", "active": 1},
            {"id": 7, "name": "Prešov", "active": 1},
        ])
        c.execute(tables["departments"].insert(), [
            {"id": 3, "name": "Výroba", "active": 1, "min_staff": 2, "branch_id": 7},
        ])
        c.execute(tables["employees"].insert(), [
            {"id": 5, "first_name": "Anna", "last_name": "Prvá",
             "position": "Op", "employment_type": "custom",
             "weekly_hours": 40, "active": 1, "branch_id": 7},
        ])
        c.execute(tables["shifts"].insert(), [
            {"id": 9, "employee_id": 5, "shift_date": "2026-10-19",
             "start_time": "06:00", "end_time": "14:00",
             "shift_type": "Ranná", "department_id": 3},
        ])
        c.execute(tables["users"].insert(), [
            {"id": 4, "username": "sef", "password_hash": "x",
             "role": "manager", "employee_id": None},
        ])
        c.execute(tables["user_branches"].insert(), [
            {"user_id": 4, "branch_id": 7},
        ])

    engine.dispose()


def test_import_sqlite_copies_data_and_fixes_sequences(pg_app, tmp_path):
    import sqlalchemy as sa

    from app.extensions import db
    from app.services.employee_service import add_employee, get_employees
    from app.services.transfer_service import copy_sqlite_to_database

    version = _fresh_migrated_postgres(pg_app)
    source = tmp_path / "src.db"
    _make_sqlite_source(source, version)

    with pg_app.app_context():
        copied = copy_sqlite_to_database(source, db.engine)

        assert copied["branches"] == 2
        assert copied["shifts"] == 1
        assert copied["user_branches"] == 1

        # Predvolená pobočka z migrácie sa nahradila zdrojovými dátami.
        names = db.session.execute(
            sa.text("SELECT name FROM branches ORDER BY id")
        ).scalars().all()
        assert names == ["Hlavná pobočka", "Prešov"]

        employees = get_employees()
        assert [(e[0], e[1], e[6]) for e in employees] == [(5, "Anna", 7)]

        # Sekvencie sú posunuté - nový záznam nenarazí na obsadené ID.
        new_id = add_employee("Boris", "Druhý", "Op", 40, branch_id=7)
        assert new_id > 5


def test_import_sqlite_refuses_non_empty_target_and_changes_nothing(
    pg_app, tmp_path
):
    import pytest as _pytest
    import sqlalchemy as sa

    from app.extensions import db
    from app.services.transfer_service import (
        TransferError,
        copy_sqlite_to_database,
    )

    version = _fresh_migrated_postgres(pg_app)
    source = tmp_path / "src.db"
    _make_sqlite_source(source, version)

    with pg_app.app_context():
        copy_sqlite_to_database(source, db.engine)

        with _pytest.raises(TransferError, match="nie je prázdna"):
            copy_sqlite_to_database(source, db.engine)

        assert db.session.execute(
            sa.text("SELECT COUNT(*) FROM shifts")
        ).scalar() == 1


def test_import_sqlite_rejects_different_schema_version(pg_app, tmp_path):
    import pytest as _pytest

    from app.extensions import db
    from app.services.transfer_service import (
        TransferError,
        copy_sqlite_to_database,
    )

    _fresh_migrated_postgres(pg_app)
    source = tmp_path / "old.db"
    _make_sqlite_source(source, "aeca62578799")

    with pg_app.app_context():
        with _pytest.raises(TransferError, match="Verzia schémy"):
            copy_sqlite_to_database(source, db.engine)


def test_import_sqlite_rolls_back_on_invalid_data(pg_app, tmp_path):
    import pytest as _pytest
    import sqlalchemy as sa

    from app.extensions import db
    from app.services.transfer_service import (
        TransferError,
        copy_sqlite_to_database,
    )

    version = _fresh_migrated_postgres(pg_app)
    source = tmp_path / "bad.db"
    _make_sqlite_source(source, version)

    # SQLite dovolí text v číselnom stĺpci, PostgreSQL nie.
    engine = sa.create_engine(f"sqlite:///{source}")
    with engine.begin() as c:
        c.execute(sa.text("UPDATE employees SET weekly_hours = 'nie-cislo'"))
    engine.dispose()

    with pg_app.app_context():
        with _pytest.raises(TransferError):
            copy_sqlite_to_database(source, db.engine)

        # Nič sa neskopírovalo (ani Prešov), ostala len predvolená pobočka.
        assert db.session.execute(
            sa.text("SELECT COUNT(*) FROM branches")
        ).scalar() == 1
        assert db.session.execute(
            sa.text("SELECT COUNT(*) FROM employees")
        ).scalar() == 0
