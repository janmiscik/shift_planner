"""Zálohovanie a obnova databázy.

SQLite
------
Záloha sa robí cez **SQLite Backup API** (``sqlite3.Connection.backup``),
nie kopírovaním súboru. Kopírovanie súboru počas zápisu nemusí dať
konzistentnú zálohu (potvrdené zmeny môžu byť ešte len v žurnáli/WAL
súbore a nie v hlavnom súbore). Backup API číta databázu konzistentne aj
vtedy, keď sa do nej práve zapisuje.

Každá záloha sa hneď po vytvorení **overí** (hlavička súboru, ``PRAGMA
integrity_check``). Zapíše sa najprv do dočasného súboru ``.part`` a
premenuje sa až po úspešnom overení, takže zlá záloha sa nikdy neobjaví
medzi hotovými. ``check_restorable`` navyše vyskúša skutočnú obnovu do
dočasného priečinka (``manage.py verify-backup``).

Obnova (``restore_backup``) prijíma len NÁZOV zálohy zo zoznamu
spravovaných záloh (nie cestu), záložný súbor pred použitím overí,
aktuálnu databázu najprv zazálohuje a nový súbor nasadí atomicky
(``os.replace``). Pri akejkoľvek chybe ostáva pôvodná databáza nezmenená.

Zálohy sa ukladajú do priečinka ``backups/`` VEDĽA súboru databázy.

PostgreSQL
----------
Zálohy cez ``pg_dump`` (``create_postgres_backup``); výsledný súbor sa
overí (hlavička ``PGDMP`` a ``pg_restore --list``, ak je dostupný).
Obnova PostgreSQL sa nerobí automaticky (pozri ``manage.py restore``).
"""

import contextlib
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from sqlalchemy.engine import make_url

from app.data.database import DEFAULT_DATABASE_PATH

SQLITE_HEADER = b"SQLite format 3\x00"
POSTGRES_HEADER = b"PGDMP"

_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class BackupError(RuntimeError):
    """Zálohu sa nepodarilo vytvoriť/obnoviť (správa je pre používateľa)."""


class BackupVerificationError(BackupError):
    """Záloha neprešla kontrolou (poškodená alebo nie je databáza)."""


def _backups_dir(database_path):
    return Path(database_path).resolve().parent / "backups"


def default_backups_dir():
    """Priečinok záloh pre PostgreSQL: ``app/data/backups/``."""

    return Path(DEFAULT_DATABASE_PATH).resolve().parent / "backups"


def _clean_label(label):
    return _LABEL_UNSAFE.sub("-", label or "").strip("-")[:40]


def _unique_backup_path(backups_dir, suffix, label):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    label = _clean_label(label)
    base = f"shift_planner_{timestamp}" + (f"_{label}" if label else "")

    candidate = backups_dir / f"{base}{suffix}"
    counter = 2

    while candidate.exists():
        candidate = backups_dir / f"{base}_{counter}{suffix}"
        counter += 1

    return candidate


# ---------------------------------------------------------------------
# SQLite: overenie
# ---------------------------------------------------------------------

def _readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def verify_sqlite_file(path, require_app_schema=False):
    """Overí, že súbor je celá a nepoškodená SQLite databáza.

    Kontroluje hlavičku súboru a ``PRAGMA integrity_check``. S
    ``require_app_schema`` vyžaduje aj tabuľku ``employees`` (že ide o
    databázu tejto appky). Vráti slovník s popisom (veľkosť, počet
    tabuliek, verzia migrácie, počty riadkov); pri probléme vyhodí
    ``BackupVerificationError``.
    """

    path = Path(path)

    if not path.is_file():
        raise BackupVerificationError(f"Súbor '{path.name}' neexistuje.")

    size = path.stat().st_size

    if size == 0:
        raise BackupVerificationError(f"Súbor '{path.name}' je prázdny.")

    with path.open("rb") as handle:
        header = handle.read(len(SQLITE_HEADER))

    if header != SQLITE_HEADER:
        raise BackupVerificationError(
            f"Súbor '{path.name}' nie je SQLite databáza."
        )

    try:
        with contextlib.closing(
            sqlite3.connect(_readonly_uri(path), uri=True, timeout=30)
        ) as connection:
            problems = [
                row[0]
                for row in connection.execute("PRAGMA integrity_check").fetchall()
            ]

            if problems != ["ok"]:
                raise BackupVerificationError(
                    f"Záloha '{path.name}' je poškodená: "
                    + "; ".join(problems[:3])
                )

            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            ]

            if require_app_schema and "employees" not in tables:
                raise BackupVerificationError(
                    f"Súbor '{path.name}' nevyzerá ako databáza tejto "
                    "appky (chýba tabuľka employees)."
                )

            alembic_version = None

            if "alembic_version" in tables:
                row = connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
                alembic_version = row[0] if row else None

            counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in sorted(tables)
            }
    except sqlite3.DatabaseError as error:
        raise BackupVerificationError(
            f"Záloha '{path.name}' sa nedá prečítať: {error}"
        ) from error

    return {
        "size": size,
        "tables": len(tables),
        "alembic_version": alembic_version,
        "counts": counts,
    }


# ---------------------------------------------------------------------
# SQLite: vytvorenie zálohy
# ---------------------------------------------------------------------

def _sqlite_backup(source_path, destination_path, source_readonly=False):
    """Konzistentná kópia databázy cez SQLite Backup API."""

    source_target = (
        _readonly_uri(source_path) if source_readonly else str(source_path)
    )

    with contextlib.closing(
        sqlite3.connect(source_target, uri=source_readonly, timeout=30)
    ) as source, contextlib.closing(
        sqlite3.connect(str(destination_path))
    ) as destination:
        source.backup(destination)


def create_backup(database_path, label=None):
    """Vytvorí overenú zálohu SQLite databázy a vráti cestu k nej.

    Používa SQLite Backup API, takže záloha je konzistentná aj počas
    zápisu do databázy. Záloha sa pred zverejnením overí; ak overenie
    zlyhá, nevznikne žiadny záložný súbor a vyhodí sa
    ``BackupVerificationError``.

    Ak súbor databázy ešte neexistuje (nová inštalácia), nič sa
    nezálohuje a vráti sa ``None``.
    """

    database_path = Path(database_path)

    if not database_path.exists():
        return None

    backups_dir = _backups_dir(database_path)
    backups_dir.mkdir(parents=True, exist_ok=True)

    backup_path = _unique_backup_path(backups_dir, ".db", label)
    partial_path = backup_path.with_name(backup_path.name + ".part")

    try:
        try:
            _sqlite_backup(database_path, partial_path)
        except sqlite3.DatabaseError as error:
            raise BackupError(
                f"Databázu sa nepodarilo zálohovať: {error}"
            ) from error

        verify_sqlite_file(partial_path)

        os.replace(partial_path, backup_path)
    finally:
        with contextlib.suppress(OSError):
            partial_path.unlink()

    return backup_path


def check_restorable(backup_path):
    """Vyskúša skutočnú OBNOVU zálohy do dočasného priečinka (skutočná
    databáza sa nemení) a vráti popis obnovenej databázy. Vyhodí
    ``BackupVerificationError``, ak sa záloha nedá obnoviť."""

    backup_path = Path(backup_path)

    info = verify_sqlite_file(backup_path, require_app_schema=True)

    with tempfile.TemporaryDirectory() as workdir:
        restored = Path(workdir) / "obnovena.db"

        try:
            _sqlite_backup(backup_path, restored, source_readonly=True)
        except sqlite3.DatabaseError as error:
            raise BackupVerificationError(
                f"Obnova zálohy '{backup_path.name}' zlyhala: {error}"
            ) from error

        restored_info = verify_sqlite_file(restored, require_app_schema=True)

    if restored_info["counts"] != info["counts"]:
        raise BackupVerificationError(
            "Obnovená databáza má iný obsah než záloha."
        )

    return restored_info


# ---------------------------------------------------------------------
# Zoznam a bezpečný výber zálohy
# ---------------------------------------------------------------------

def _scan_backups(backups_dir):
    backups_dir = Path(backups_dir)

    if not backups_dir.exists():
        return []

    backups = []

    for backup_path in [
        *backups_dir.glob("*.db"),
        *backups_dir.glob("*.dump"),
    ]:
        stat = backup_path.stat()
        backups.append(
            (
                backup_path,
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime),
            )
        )

    backups.sort(key=lambda item: item[2], reverse=True)

    return backups


def list_backups(database_path):
    """Vráti zoznam záloh (najnovšia prvá) - (cesta, veľkosť, čas)."""

    return _scan_backups(_backups_dir(database_path))


def resolve_managed_backup(
    backup_filename, database_path=None, suffix=".db", backups_dir=None
):
    """Vráti cestu k zálohe zo zoznamu spravovaných záloh.

    Prijíma len jednoduchý NÁZOV súboru (žiadna cesta, ``..``, lomky,
    absolútna cesta ani nulový znak) a len súbor, ktorý je v zozname
    záloh a fyzicky leží priamo v priečinku záloh (nie je to odkaz
    smerujúci mimo). Inak vyhodí ``BackupError`` (neplatný názov alebo
    typ) alebo ``FileNotFoundError`` (záloha v zozname nie je).
    """

    if backups_dir is None:
        backups_dir = _backups_dir(database_path)

    backups_dir = Path(backups_dir)

    if (
        not isinstance(backup_filename, str)
        or not backup_filename.strip()
        or "\x00" in backup_filename
        or backup_filename in (".", "..")
        or PurePosixPath(backup_filename).name != backup_filename
        or PureWindowsPath(backup_filename).name != backup_filename
    ):
        raise BackupError(
            "Neplatný názov zálohy - zadaj len názov súboru zo zoznamu "
            "(python manage.py list-backups), nie cestu."
        )

    if not backup_filename.endswith(suffix):
        raise BackupError(
            f"Nepodporovaný typ zálohy '{backup_filename}' "
            f"(očakáva sa {suffix})."
        )

    managed = {
        path.name: path
        for path, _size, _created in _scan_backups(backups_dir)
        if path.suffix == suffix
    }

    if backup_filename not in managed:
        raise FileNotFoundError(
            f"Záloha '{backup_filename}' neexistuje v zozname spravovaných "
            "záloh."
        )

    backup_path = managed[backup_filename]

    if (
        backup_path.is_symlink()
        or not backup_path.is_file()
        or backup_path.resolve().parent != backups_dir.resolve()
    ):
        raise BackupError(
            f"Záloha '{backup_filename}' nie je bežný súbor v priečinku záloh."
        )

    return backup_path


# ---------------------------------------------------------------------
# SQLite: obnova
# ---------------------------------------------------------------------

def restore_backup(backup_filename, database_path):
    """Obnoví databázu zo zálohy.

    Postup (pri akejkoľvek chybe ostáva pôvodná databáza nezmenená):
      1. názov sa overí proti zoznamu spravovaných záloh (nie je to
         cesta; súbor leží v priečinku záloh),
      2. záložný súbor sa overí (SQLite, ``integrity_check``, databáza
         tejto appky),
      3. AKTUÁLNA databáza sa zazálohuje pod označením "pred-obnovou",
      4. záloha sa skopíruje do dočasného súboru vedľa databázy, znova
         sa overí a atomicky nahradí databázu (``os.replace``),
      5. zostatky žurnálu (``-wal``, ``-shm``, ``-journal``) starej
         databázy sa odstránia.

    Obnovu rob so ZASTAVENOU appkou (na Windowse sa súbor v používaní
    nedá nahradiť - obnova vtedy zlyhá a nič sa nezmení).

    Vyhodí ``FileNotFoundError``, ak záloha v zozname nie je, a
    ``BackupError`` pri neplatnom názve, poškodenej zálohe alebo
    zlyhaní obnovy.
    """

    database_path = Path(database_path)

    backup_path = resolve_managed_backup(
        backup_filename, database_path, suffix=".db"
    )

    verify_sqlite_file(backup_path, require_app_schema=True)

    create_backup(database_path, label="pred-obnovou")

    restoring_path = database_path.with_name(database_path.name + ".restoring")

    try:
        with contextlib.suppress(OSError):
            restoring_path.unlink()

        _sqlite_backup(backup_path, restoring_path, source_readonly=True)
        verify_sqlite_file(restoring_path, require_app_schema=True)

        os.replace(restoring_path, database_path)
    except BackupError:
        raise
    except Exception as error:
        raise BackupError(
            "Obnova zlyhala, pôvodná databáza ostala nezmenená "
            f"(zastav appku a skús znova): {error}"
        ) from error
    finally:
        with contextlib.suppress(OSError):
            restoring_path.unlink()

    for suffix in _SIDECAR_SUFFIXES:
        with contextlib.suppress(OSError):
            database_path.with_name(database_path.name + suffix).unlink()

    return database_path


# ---------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------

def verify_postgres_dump(path):
    """Overí, že súbor je čitateľný výpis ``pg_dump`` (hlavička ``PGDMP``
    a, ak je ``pg_restore`` dostupný, ``pg_restore --list``)."""

    path = Path(path)

    if not path.is_file() or path.stat().st_size == 0:
        raise BackupVerificationError(
            f"Súbor '{path.name}' je prázdny alebo chýba."
        )

    with path.open("rb") as handle:
        if handle.read(len(POSTGRES_HEADER)) != POSTGRES_HEADER:
            raise BackupVerificationError(
                f"Súbor '{path.name}' nie je výpis pg_dump."
            )

    pg_restore = shutil.which("pg_restore")

    if pg_restore is not None:
        result = subprocess.run(
            [pg_restore, "--list", str(path)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise BackupVerificationError(
                f"Výpis '{path.name}' sa nedá prečítať: "
                + (result.stderr.strip() or "pg_restore skončil s chybou.")
            )

    return {
        "size": path.stat().st_size,
        "checked_with_pg_restore": pg_restore is not None,
    }


def create_postgres_backup(database_uri, label=None, backups_dir=None):
    """Vytvorí zálohu PostgreSQL databázy cez ``pg_dump`` (formát
    ``custom``, súbor ``.dump``) a vráti cestu k nej.

    Vyžaduje ``pg_dump`` v PATH. Heslo sa nepredáva v príkazovom riadku,
    ale cez ``PGPASSWORD``. Hotový výpis sa overí; pri chybe vyhodí
    ``BackupError`` a nenechá po sebe súbor.
    """

    pg_dump = shutil.which("pg_dump")

    if pg_dump is None:
        raise BackupError(
            "na serveri nie je nainštalovaný 'pg_dump' (klientske "
            "nástroje PostgreSQL)."
        )

    url = make_url(str(database_uri))

    backups_dir = Path(backups_dir) if backups_dir else default_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)

    backup_path = _unique_backup_path(backups_dir, ".dump", label)

    command = [pg_dump, "--format=custom", "--file", str(backup_path)]

    if url.host:
        command += ["--host", url.host]

    if url.port:
        command += ["--port", str(url.port)]

    if url.username:
        command += ["--username", url.username]

    command.append(url.database)

    environment = dict(os.environ)

    if url.password:
        environment["PGPASSWORD"] = url.password

    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        backup_path.unlink(missing_ok=True)

        raise BackupError(
            result.stderr.strip() or "pg_dump skončil s chybou."
        )

    try:
        verify_postgres_dump(backup_path)
    except BackupVerificationError:
        backup_path.unlink(missing_ok=True)
        raise

    return backup_path
