"""Testy bezpečnosti záloh a obnovy (SQLite Backup API, overenie záloh,
obnova len zo spravovaných záloh, správanie pri zlyhaní)."""

import os
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from app.services import backup_service
from app.services.backup_service import (
    BackupError,
    BackupVerificationError,
    check_restorable,
    create_backup,
    create_postgres_backup,
    list_backups,
    resolve_managed_backup,
    restore_backup,
    verify_postgres_dump,
    verify_sqlite_file,
)


def make_db(path, names=("Anna", "Boris"), alembic="abc123", rows=0):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS employees (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT)")
    connection.execute("DELETE FROM employees")
    connection.execute("DELETE FROM alembic_version")
    connection.execute("INSERT INTO alembic_version VALUES (?)", (alembic,))
    connection.executemany("INSERT INTO employees (name) VALUES (?)", [(n,) for n in names])
    connection.executemany("INSERT INTO employees (name) VALUES (?)", [(f"riadok{i}",) for i in range(rows)])
    connection.commit()
    connection.close()


def names(path):
    connection = sqlite3.connect(path)

    try:
        return sorted(r[0] for r in connection.execute("SELECT name FROM employees"))
    finally:
        connection.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shift_planner.db"
    make_db(path)

    return path


def backups_dir(db_path):
    return db_path.parent / "backups"


# ---------------------------------------------------------------------
# Konzistencia zálohy
# ---------------------------------------------------------------------

def test_file_copy_misses_committed_wal_data_but_backup_api_does_not(db, tmp_path):
    """Dôkaz problému so shutil.copy2: v režime WAL sú potvrdené zmeny
    ešte len v -wal súbore, nie v hlavnom súbore. Záloha cez Backup API
    ich obsahuje."""

    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executemany("INSERT INTO employees (name) VALUES (?)", [(f"w{i}",) for i in range(50)])
    writer.commit()

    try:
        naive_copy = tmp_path / "naive_copy.db"
        shutil.copy2(db, naive_copy)

        assert len(names(naive_copy)) == 2                 # prišlo sa o 50 potvrdených riadkov

        backup = create_backup(db)

        assert len(names(backup)) == 52
        assert verify_sqlite_file(backup, require_app_schema=True)["counts"]["employees"] == 52
    finally:
        writer.close()


def test_backup_is_consistent_while_another_connection_is_writing(db):
    """Zapisovateľ vkladá vždy DVOJICE riadkov v jednej transakcii. Každá
    záloha vytvorená počas toho musí obsahovať len úplné dvojice."""

    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE pairs (pair_id INTEGER, side TEXT)")
    connection.commit()
    connection.close()

    stop = threading.Event()
    errors = []

    def writer():
        conn = sqlite3.connect(db, timeout=30)

        try:
            pair = 0

            while not stop.is_set():
                pair += 1
                conn.execute("INSERT INTO pairs VALUES (?, 'a')", (pair,))
                conn.execute("INSERT INTO pairs VALUES (?, 'b')", (pair,))
                conn.commit()
        except Exception as error:   # pragma: no cover
            errors.append(error)
        finally:
            conn.close()

    thread = threading.Thread(target=writer)
    thread.start()

    try:
        backups = [create_backup(db, label=f"b{i}") for i in range(15)]
    finally:
        stop.set()
        thread.join(timeout=10)

    assert not errors

    for backup in backups:
        connection = sqlite3.connect(backup)

        try:
            incomplete = connection.execute(
                "SELECT COUNT(*) FROM (SELECT pair_id FROM pairs GROUP BY pair_id HAVING COUNT(*) != 2)"
            ).fetchone()[0]
        finally:
            connection.close()

        assert incomplete == 0, backup.name


def test_backup_name_and_label_are_sanitised_and_never_overwrite(db):
    first = create_backup(db, label="../../útok s medzerami")
    second = create_backup(db, label="../../útok s medzerami")

    assert first.parent == backups_dir(db) and second.parent == backups_dir(db)
    assert first != second and first.exists() and second.exists()
    assert "/" not in first.name and "\\" not in first.name and ".." not in first.name

    assert len(list_backups(db)) == 2


def test_backup_does_not_leave_partial_files(db):
    create_backup(db)

    assert [p.name for p in backups_dir(db).iterdir() if p.suffix == ".part"] == []


# ---------------------------------------------------------------------
# Overenie
# ---------------------------------------------------------------------

def test_verify_accepts_a_good_backup_and_describes_it(db):
    backup = create_backup(db)

    info = verify_sqlite_file(backup, require_app_schema=True)

    assert info["alembic_version"] == "abc123"
    assert info["counts"] == {"alembic_version": 1, "employees": 2}
    assert info["tables"] == 2 and info["size"] > 0


def test_backup_of_a_corrupted_database_is_refused_and_leaves_nothing(tmp_path):
    broken = tmp_path / "shift_planner.db"
    broken.write_bytes(b"toto nie je databaza" * 100)

    with pytest.raises(BackupError):
        create_backup(broken)

    directory = backups_dir(broken)
    assert not directory.exists() or list(directory.iterdir()) == []


@pytest.mark.parametrize("kind", ["empty", "text", "truncated", "missing"])
def test_verify_rejects_bad_files(tmp_path, kind):
    path = tmp_path / "x.db"

    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "text":
        path.write_text("ahoj, toto nie je databaza" * 50)
    elif kind == "truncated":
        make_db(path, rows=3000)
        size = path.stat().st_size
        path.write_bytes(path.read_bytes()[: size // 2])

    with pytest.raises(BackupVerificationError):
        verify_sqlite_file(path)


def test_verify_requires_the_app_schema_only_when_asked(tmp_path):
    other = tmp_path / "cudzia.db"
    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE poznamky (text TEXT)")
    connection.commit()
    connection.close()

    assert verify_sqlite_file(other)["tables"] == 1

    with pytest.raises(BackupVerificationError, match="employees"):
        verify_sqlite_file(other, require_app_schema=True)


def test_check_restorable_does_a_real_restore_into_a_temp_dir(db):
    backup = create_backup(db)

    info = check_restorable(backup)

    assert info["counts"]["employees"] == 2
    assert names(db) == ["Anna", "Boris"]                 # skutočná databáza sa nezmenila


def test_check_restorable_fails_for_a_damaged_backup(db):
    backup = create_backup(db)
    backup.write_bytes(backup.read_bytes()[:200])

    with pytest.raises(BackupVerificationError):
        check_restorable(backup)


# ---------------------------------------------------------------------
# Obnova: validácia názvu
# ---------------------------------------------------------------------

@pytest.fixture
def db_with_backup(db):
    backup = create_backup(db, label="povodna")
    make_db(db, names=("Zmenený",))                       # súčasný stav sa odlišuje od zálohy

    # Platná databáza MIMO priečinka záloh - nesmie sa dať použiť.
    make_db(db.parent / "outside.db", names=("Cudzí",))

    return db, backup


@pytest.mark.parametrize(
    "name",
    [
        "../outside.db",
        "..\\outside.db",
        "../../outside.db",
        "backups/../outside.db",
        "/etc/passwd.db",
        "C:\\Windows\\x.db",
        "C:/x.db",
        "podpriecinok/zaloha.db",
        "zaloha.db\x00.txt",
        "",
        "   ",
        ".",
        "..",
        None,
        123,
        ["a.db"],
    ],
)
def test_restore_rejects_path_like_and_invalid_names(db_with_backup, name):
    db, backup = db_with_backup
    before = [p.name for p, _, _ in list_backups(db)]

    with pytest.raises(BackupError):
        restore_backup(name, db)

    assert names(db) == ["Zmenený"]                       # databáza nezmenená
    assert [p.name for p, _, _ in list_backups(db)] == before   # ani záložný súbor "pred-obnovou"


def test_restore_accepts_only_names_from_the_managed_backup_list(db_with_backup):
    db, _ = db_with_backup

    # Platný jednoduchý názov, súbor však existuje len mimo priečinka záloh.
    with pytest.raises(FileNotFoundError):
        restore_backup("outside.db", db)

    with pytest.raises(FileNotFoundError):
        restore_backup("neexistuje.db", db)

    assert names(db) == ["Zmenený"]


@pytest.mark.parametrize("name", ["poznamky.txt", "zaloha.db.part", "zaloha.dump", "zaloha.sql"])
def test_restore_rejects_unsupported_file_types(db_with_backup, name):
    db, _ = db_with_backup
    (backups_dir(db) / name).write_bytes(b"obsah")

    with pytest.raises(BackupError):
        restore_backup(name, db)

    assert names(db) == ["Zmenený"]


def test_restore_rejects_a_symlink_pointing_outside_the_backup_directory(db_with_backup):
    db, _ = db_with_backup
    link = backups_dir(db) / "odkaz.db"

    try:
        os.symlink(db.parent / "outside.db", link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolické odkazy nie sú v tomto prostredí dostupné")

    with pytest.raises(BackupError):
        restore_backup("odkaz.db", db)

    assert names(db) == ["Zmenený"]


def test_resolve_managed_backup_returns_a_path_inside_the_directory(db_with_backup):
    db, backup = db_with_backup

    resolved = resolve_managed_backup(backup.name, db)

    assert resolved == backup and resolved.parent == backups_dir(db).resolve()


# ---------------------------------------------------------------------
# Obnova: priebeh a zlyhania
# ---------------------------------------------------------------------

def test_restore_replaces_the_database_and_keeps_a_safety_backup(db_with_backup):
    db, backup = db_with_backup

    for suffix in ("-wal", "-shm", "-journal"):
        db.with_name(db.name + suffix).write_bytes(b"zostatok starej databazy")

    restored = restore_backup(backup.name, db)

    assert restored == db
    assert names(db) == ["Anna", "Boris"]
    assert verify_sqlite_file(db, require_app_schema=True)["counts"]["employees"] == 2

    # Starý stav zachránený v zálohe "pred-obnovou".
    safety = [p for p, _, _ in list_backups(db) if "pred-obnovou" in p.name]
    assert len(safety) == 1 and names(safety[0]) == ["Zmenený"]

    # Žiadne zostatky.
    for suffix in ("-wal", "-shm", "-journal", ".restoring"):
        assert not db.with_name(db.name + suffix).exists()


def test_restore_onto_a_missing_database_works_without_a_safety_backup(tmp_path):
    source = tmp_path / "zdroj" / "shift_planner.db"
    source.parent.mkdir()
    make_db(source)
    backup = create_backup(source)

    # Nový stroj: databáza ešte neexistuje, zálohy sú vedľa nej.
    target = tmp_path / "novy" / "shift_planner.db"
    (target.parent / "backups").mkdir(parents=True)
    shutil.copy2(backup, target.parent / "backups" / backup.name)

    restore_backup(backup.name, target)

    assert names(target) == ["Anna", "Boris"]
    assert [p.name for p, _, _ in list_backups(target)] == [backup.name]


def test_restore_refuses_a_corrupted_backup_before_touching_anything(db_with_backup):
    db, backup = db_with_backup
    backup.write_bytes(backup.read_bytes()[:150])

    with pytest.raises(BackupVerificationError):
        restore_backup(backup.name, db)

    assert names(db) == ["Zmenený"]
    # Ani záložný súbor "pred-obnovou" nevznikol (záloha sa overuje ako prvá).
    assert [p.name for p, _, _ in list_backups(db)] == [backup.name]


def test_restore_refuses_a_backup_of_a_foreign_database(db_with_backup):
    db, _ = db_with_backup
    foreign = backups_dir(db) / "shift_planner_2020-01-01_000000_cudzia.db"
    connection = sqlite3.connect(foreign)
    connection.execute("CREATE TABLE nieco (x TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(BackupVerificationError, match="employees"):
        restore_backup(foreign.name, db)

    assert names(db) == ["Zmenený"]


def test_failed_replace_leaves_the_original_database_untouched(db_with_backup, monkeypatch):
    db, backup = db_with_backup
    real_replace = os.replace          # os je spoločný modul, odkaz si uložíme PRED náhradou

    def failing_replace(source, target):
        if Path(target) == db:
            raise PermissionError("súbor je používaný inou aplikáciou")

        return real_replace(source, target)

    monkeypatch.setattr(backup_service.os, "replace", failing_replace)

    with pytest.raises(BackupError, match="ostala nezmenená"):
        restore_backup(backup.name, db)

    assert names(db) == ["Zmenený"]
    assert not db.with_name(db.name + ".restoring").exists()          # dočasný súbor upratany


def test_failure_while_copying_leaves_the_original_database_untouched(db_with_backup, monkeypatch):
    db, backup = db_with_backup
    real = backup_service._sqlite_backup

    def failing_copy(source, destination, source_readonly=False):
        if str(destination).endswith(".restoring"):
            raise sqlite3.OperationalError("disk plný")

        return real(source, destination, source_readonly)

    monkeypatch.setattr(backup_service, "_sqlite_backup", failing_copy)

    with pytest.raises(BackupError):
        restore_backup(backup.name, db)

    assert names(db) == ["Zmenený"]
    assert not db.with_name(db.name + ".restoring").exists()


# ---------------------------------------------------------------------
# PostgreSQL výpisy (bez potreby PostgreSQL)
# ---------------------------------------------------------------------

def test_verify_postgres_dump_checks_header_and_pg_restore(tmp_path, monkeypatch):
    empty = tmp_path / "prazdny.dump"
    empty.write_bytes(b"")
    garbage = tmp_path / "zly.dump"
    garbage.write_bytes(b"toto nie je dump")
    good = tmp_path / "dobry.dump"
    good.write_bytes(b"PGDMP\x01\x0e\x00 zvysok")

    for bad in (empty, garbage, tmp_path / "chyba.dump"):
        with pytest.raises(BackupVerificationError):
            verify_postgres_dump(bad)

    # Bez pg_restore sa skontroluje aspoň hlavička.
    monkeypatch.setattr(backup_service.shutil, "which", lambda name: None)
    assert verify_postgres_dump(good) == {"size": good.stat().st_size, "checked_with_pg_restore": False}

    # S pg_restore: zlyhanie "--list" = nepoužiteľný výpis.
    monkeypatch.setattr(backup_service.shutil, "which", lambda name: "/usr/bin/pg_restore")
    monkeypatch.setattr(
        backup_service.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "pg_restore: chyba: poškodený výpis"),
    )

    with pytest.raises(BackupVerificationError, match="poškodený výpis"):
        verify_postgres_dump(good)

    monkeypatch.setattr(
        backup_service.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "obsah", ""),
    )
    assert verify_postgres_dump(good)["checked_with_pg_restore"] is True


def test_postgres_backup_that_fails_verification_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(command, **kwargs):
        if command[0].endswith("pg_restore"):
            return subprocess.CompletedProcess(command, 1, "", "pg_restore: nečitateľné")

        Path(command[command.index("--file") + 1]).write_bytes(b"PGDMP-ale-poskodeny")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backup_service.subprocess, "run", run)

    with pytest.raises(BackupVerificationError):
        create_postgres_backup("postgresql://u:p@localhost/db", backups_dir=tmp_path)

    assert list(tmp_path.glob("*.dump")) == []


# ---------------------------------------------------------------------
# Príkazy správcu (len SQLite - používajú predvolenú databázu)
# ---------------------------------------------------------------------

def manage(monkeypatch, *args):
    import sys

    import manage as manage_module

    monkeypatch.setattr(sys, "argv", ["manage.py", *args])
    manage_module.main()


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Predvolená databáza príkazov presmerovaná do dočasného priečinka."""

    import app.data.database as database_module

    path = tmp_path / "shift_planner.db"
    make_db(path)
    monkeypatch.setattr(database_module, "DEFAULT_DATABASE_PATH", path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    return path


@pytest.mark.sqlite_only
def test_verify_backup_command(cli_db, monkeypatch, capsys):
    backup = create_backup(cli_db, label="tyzdenna")

    manage(monkeypatch, "verify-backup")
    out = capsys.readouterr().out
    assert f"'{backup.name}' je v poriadku" in out
    assert "skúšobná obnova do dočasného priečinka: ok" in out
    assert "employees: 2" in out and "abc123" in out

    manage(monkeypatch, "verify-backup", backup.name)
    assert "je v poriadku" in capsys.readouterr().out


@pytest.mark.sqlite_only
def test_verify_backup_command_reports_damage_and_missing_backups(cli_db, monkeypatch, capsys):
    with pytest.raises(SystemExit) as no_backups:
        manage(monkeypatch, "verify-backup")
    assert no_backups.value.code == 1 and "Žiadne zálohy" in capsys.readouterr().out

    backup = create_backup(cli_db)
    backup.write_bytes(backup.read_bytes()[:100])

    with pytest.raises(SystemExit) as damaged:
        manage(monkeypatch, "verify-backup")
    assert damaged.value.code == 1 and "NEPOUŽITEĽNÁ" in capsys.readouterr().out

    for argument in ("../x.db", "neexistuje.db"):
        with pytest.raises(SystemExit):
            manage(monkeypatch, "verify-backup", argument)


@pytest.mark.sqlite_only
def test_restore_command_requires_confirmation_and_rejects_bad_names(cli_db, monkeypatch, capsys):
    backup = create_backup(cli_db, label="povodna")
    make_db(cli_db, names=("Zmenený",))

    # Bez potvrdenia sa nič nezmení.
    monkeypatch.setattr("builtins.input", lambda prompt="": "nie")
    manage(monkeypatch, "restore", backup.name)
    assert "Zrušené" in capsys.readouterr().out and names(cli_db) == ["Zmenený"]

    # Cesta namiesto názvu = odmietnuté, exit 1.
    for bad in ("../outside.db", str(backup), "neexistuje.db"):
        with pytest.raises(SystemExit) as exit_info:
            manage(monkeypatch, "restore", bad, "--yes")

        assert exit_info.value.code == 1

    assert names(cli_db) == ["Zmenený"]

    with pytest.raises(SystemExit):
        manage(monkeypatch, "restore")                                  # chýba názov

    manage(monkeypatch, "restore", backup.name, "--yes")
    assert "Databáza obnovená" in capsys.readouterr().out
    assert names(cli_db) == ["Anna", "Boris"]

    monkeypatch.setattr("builtins.input", lambda prompt="": "ano")
    make_db(cli_db, names=("Znova zmenený",))
    manage(monkeypatch, "restore", backup.name)
    assert names(cli_db) == ["Anna", "Boris"]


@pytest.mark.sqlite_only
def test_restore_command_reports_a_corrupted_backup_without_changing_anything(cli_db, monkeypatch, capsys):
    backup = create_backup(cli_db)
    make_db(cli_db, names=("Zmenený",))
    backup.write_bytes(b"poskodeny obsah")

    with pytest.raises(SystemExit) as exit_info:
        manage(monkeypatch, "restore", backup.name, "--yes")

    assert exit_info.value.code == 1
    assert "ostala nezmenená" in capsys.readouterr().out
    assert names(cli_db) == ["Zmenený"]


@pytest.mark.sqlite_only
def test_manual_backup_command_creates_a_verified_backup(cli_db, monkeypatch, capsys):
    manage(monkeypatch, "backup")

    assert "Záloha vytvorená" in capsys.readouterr().out
    assert len(list_backups(cli_db)) == 1


@pytest.mark.sqlite_only
def test_migrate_does_not_run_when_the_backup_fails(cli_db, monkeypatch, capsys):
    def failing_backup(path, label=None):
        raise BackupError("disk je plný")

    monkeypatch.setattr(backup_service, "create_backup", failing_backup)

    with pytest.raises(SystemExit) as exit_info:
        manage(monkeypatch, "migrate")

    out = capsys.readouterr().out
    assert exit_info.value.code == 1
    assert "Migrácia sa NESPUSTILA" in out and "disk je plný" in out
