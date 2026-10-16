"""Zálohovanie SQLite databázy.

Zálohy sa ukladajú do priečinka ``backups/`` VEDĽA samotného súboru
databázy (nie na pevne danom mieste v appke) - vďaka tomu si testy so
svojou dočasnou databázou nikdy neznečistia zálohy reálneho projektu.
Bežná appka teda zálohuje do ``app/data/backups/`` (mimo gitu - pozri
.gitignore), pretože tam je aj samotný súbor ``shift_planner.db``.
"""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.data.database import DEFAULT_DATABASE_PATH


class BackupError(RuntimeError):
    """Zálohu sa nepodarilo vytvoriť (napr. chýba ``pg_dump``)."""


def _backups_dir(database_path):
    return Path(database_path).resolve().parent / "backups"


def create_backup(database_path, label=None):
    """Vytvorí zálohu databázy a vráti cestu k záložnému súboru.

    Ak zdrojový súbor databázy ešte neexistuje (napr. úplne nová
    inštalácia), nič sa nezálohuje a vráti sa ``None`` - niet čo
    zálohovať.
    """

    database_path = Path(database_path)

    if not database_path.exists():
        return None

    backups_dir = _backups_dir(database_path)
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    backup_path = backups_dir / f"shift_planner_{timestamp}{suffix}.db"

    shutil.copy2(database_path, backup_path)

    return backup_path


def default_backups_dir():
    """Priečinok záloh pre PostgreSQL (nemá súbor, vedľa ktorého by sa
    zálohy ukladali): ``app/data/backups/``."""

    return Path(DEFAULT_DATABASE_PATH).resolve().parent / "backups"


def create_postgres_backup(database_uri, label=None, backups_dir=None):
    """Vytvorí zálohu PostgreSQL databázy cez ``pg_dump`` (formát
    ``custom``, súbor ``.dump``) a vráti cestu k nej.

    Vyžaduje nainštalované klientske nástroje PostgreSQL (``pg_dump``
    v PATH). Heslo sa nepredáva v príkazovom riadku, ale cez premennú
    prostredia ``PGPASSWORD``. Vyhodí ``BackupError``, ak sa záloha
    nepodarí.
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

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    backup_path = backups_dir / f"shift_planner_{timestamp}{suffix}.dump"

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

    return backup_path


def list_backups(database_path):
    """Vráti zoznam záloh (najnovšia prvá) - (cesta, veľkosť, čas)."""

    backups_dir = _backups_dir(database_path)

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


def restore_backup(backup_filename, database_path):
    """Obnoví databázu zo zálohy.

    Pred samotným obnovením najprv zálohuje AKTUÁLNY (možno pokazený)
    stav databázy pod označením "pred-obnovou", aby sa dala vrátiť aj
    táto operácia, keby bola omylom.

    Vráti cestu k obnovenej databáze. Vyhodí ``FileNotFoundError``,
    ak zálohový súbor neexistuje.
    """

    database_path = Path(database_path)
    backup_path = _backups_dir(database_path) / backup_filename

    if not backup_path.exists():
        raise FileNotFoundError(f"Záloha '{backup_filename}' neexistuje.")

    create_backup(database_path, label="pred-obnovou")

    shutil.copy2(backup_path, database_path)

    return database_path
