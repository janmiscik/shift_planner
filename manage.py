"""Správcovské príkazy pre Shift Planner.

Použitie:
    python manage.py migrate               # aplikuje databázové migrácie
                                            # (najprv automaticky zálohuje
                                            # existujúcu databázu)
    python manage.py import-sqlite [cesta] # jednorazovo skopíruje dáta zo
                                            # SQLite do PostgreSQL
                                            # (DATABASE_URL), pozri
                                            # DEPLOYMENT.md
    python manage.py audit-prune <dni>     # zmaže záznamy zmien (audit log)
                                            # staršie ako <dni> (min. 30)
    python manage.py serve                 # spustí produkčný server
                                            # (waitress); vyžaduje vlastný
                                            # SECRET_KEY, pozri DEPLOYMENT.md
    python manage.py runserver             # spustí vývojový webový server
                                            # (HOST, PORT, FLASK_DEBUG sa dajú
                                            # nastaviť premennými prostredia;
                                            # pri inom hoste než localhost je
                                            # debug vždy vypnutý)
    python manage.py create-admin <meno>   # vytvorí administrátorský účet
                                            # (meno je nepovinné, opýta sa)
    python manage.py create-manager <meno> # vytvorí manažérsky účet
                                            # (opýta sa na pobočky, ktoré
                                            # bude spravovať)
    python manage.py backup                # ručne zálohuje databázu
    python manage.py list-backups          # vypíše dostupné zálohy
    python manage.py restore <nazov_suboru> # obnoví databázu zo zálohy
    python manage.py cleanup-duplicates    # zmaže PRESNE identické duplicity
    python manage.py find-overlaps         # nájde prekrývajúce sa smeny
                                            # (aj keď nie sú identické) -
                                            # nič nemaže, len vypíše
    python manage.py backfill-departments  # doplní oddelenie do starších
                                            # smien, kde chýba a je to
                                            # jednoznačné

Databáza je od tejto verzie spravovaná cez SQLAlchemy + Alembic
(``python manage.py migrate``). Vstavaná detekcia rozozná tri stavy:
  - úplne nová databáza -> vytvorí všetky tabuľky od začiatku,
  - existujúca databáza zo staršej verzie appky (pred prechodom na
    Alembic) -> jej schéma je už aktuálna, len sa označí ako taká
    (Alembic "stamp"), nič sa neprepisuje ani nemaže,
  - databáza už spravovaná Alembicom -> normálna aktualizácia.

Pred KAŽDOU migráciou existujúcej databázy sa automaticky vytvorí
záloha (do ``app/data/backups/``), pre prípad, že by sa niečo
pokazilo.

Databáza: predvolene SQLite súbor. Pre nasadenie na viac pobočiek
nastav v ``.env`` premennú ``DATABASE_URL`` (PostgreSQL), napr.
``postgresql://uzivatel:heslo@localhost:5432/shift_planner``. Zálohy sa
potom robia cez ``pg_dump`` (musí byť nainštalovaný).
"""

import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "migrate":
        _run_migrate()

    elif command == "runserver":
        import os

        from app.web.app import create_app

        host = os.environ.get("HOST", "127.0.0.1")
        port = int(os.environ.get("PORT", "5000"))

        # Werkzeug debugger dovoľuje spúšťať kód - nikdy ho nezapíname,
        # ak je server dostupný zo siete (iný host než localhost).
        debug = os.environ.get("FLASK_DEBUG", "1") == "1" and host in (
            "127.0.0.1",
            "localhost",
        )

        create_app().run(host=host, port=port, debug=debug)

    elif command == "serve":
        _run_serve()

    elif command == "audit-prune":
        _run_audit_prune(sys.argv[2] if len(sys.argv) > 2 else None)

    elif command == "import-sqlite":
        _run_import_sqlite(sys.argv[2] if len(sys.argv) > 2 else None)

    elif command == "create-admin":
        _run_create_user(
            sys.argv[2] if len(sys.argv) > 2 else None, "admin"
        )

    elif command == "create-manager":
        _run_create_user(
            sys.argv[2] if len(sys.argv) > 2 else None, "manager"
        )

    elif command == "status":
        _run_status()

    elif command == "backup":
        _run_backup()

    elif command == "list-backups":
        _run_list_backups()

    elif command == "restore":
        if len(sys.argv) < 3:
            print(
                "Použitie: python manage.py restore <nazov_zalohy>\n"
                "Zoznam dostupných záloh: python manage.py list-backups"
            )
            return

        _run_restore(sys.argv[2])

    elif command == "cleanup-duplicates":
        from app.web.app import create_app
        from app.services.shift_service import remove_duplicate_shifts

        app = create_app()

        with app.app_context():
            removed = remove_duplicate_shifts()

        if removed:
            print(f"Odstránených duplicitných smien: {len(removed)}")
            print(f"ID zmazaných smien: {removed}")
        else:
            print("Žiadne duplicitné smeny sa nenašli.")

    elif command == "find-overlaps":
        from app.web.app import create_app
        from app.services.shift_service import find_overlapping_shifts

        app = create_app()

        with app.app_context():
            conflicts = find_overlapping_shifts()

        if not conflicts:
            print("Žiadne prekrývajúce sa smeny sa nenašli.")
        else:
            print(f"Nájdených konfliktných dvojíc: {len(conflicts)}\n")

            for row, other in conflicts:
                name = f"{row[1]} {row[2]}"
                print(f"Zamestnanec: {name}")
                print(
                    f"  smena #{row[0]}: {row[4]} {row[5]}-{row[6]}"
                )
                print(
                    f"  smena #{other[0]}: {other[4]} {other[5]}-{other[6]}"
                )
                print(
                    "  -> obe naraz nemôžu byť správne, jednu z nich "
                    "zmaž na stránke /shifts v appke.\n"
                )

    elif command == "backfill-departments":
        from app.web.app import create_app
        from app.services.shift_service import backfill_shift_departments

        app = create_app()

        with app.app_context():
            updated, skipped = backfill_shift_departments()

        print(f"Doplnené oddelenie do {updated} starších smien.")

        if skipped:
            print(
                f"Preskočených {skipped} smien - zamestnanec má "
                "priradené 0 alebo viac ako 1 oddelenie, takže sa "
                "nedalo jednoznačne uhádnuť ktoré. Doplň to ručne "
                "cez úpravu smeny v appke."
            )

    else:
        print(f"Neznámy príkaz: {command}")
        print(__doc__)


def _run_status():
    from pathlib import Path

    import sqlalchemy as sa

    from app.data.database import (
        DEFAULT_DATABASE_PATH,
        is_sqlite_uri,
        redact_database_uri,
    )
    from app.extensions import db
    from app.web.app import create_app

    uri = _database_uri()

    if is_sqlite_uri(uri):
        db_path = Path(DEFAULT_DATABASE_PATH)

        print(f"Databázový súbor: {db_path}")
        print(f"Existuje: {'áno' if db_path.exists() else 'NIE'}")

        if not db_path.exists():
            print("Spusti najprv: python manage.py migrate")
            return

        print(f"Veľkosť: {db_path.stat().st_size / 1024:.1f} KB")
    else:
        print(f"Databáza: {redact_database_uri(uri)}")

    print()

    app = create_app()

    with app.app_context():
        inspector = sa.inspect(db.engine)
        tables = set(inspector.get_table_names())

        if "alembic_version" in tables:
            version = db.session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()
            print(f"Alembic verzia: {version}")
        else:
            print("Alembic verzia: ŽIADNA (schéma nie je spravovaná)")

        print()
        print("Počty záznamov:")

        for table in (
            "branches",
            "employees",
            "departments",
            "employee_departments",
            "shifts",
            "absences",
            "users",
        ):
            if table in tables:
                count = db.session.execute(
                    sa.text(f"SELECT COUNT(*) FROM {table}")
                ).scalar()
                print(f"  {table}: {count}")
            else:
                print(f"  {table}: (tabuľka neexistuje)")


def _database_uri():
    """URI databázy, na ktorú appka mieri (explicitne cez DATABASE_URL
    v prostredí/.env, inak predvolený SQLite súbor)."""

    import app.extensions  # noqa: F401  (načíta .env)
    from app.data.database import resolve_database_uri

    return resolve_database_uri()


def _run_import_sqlite(sqlite_path=None):
    """Jednorazový presun dát zo SQLite súboru do PostgreSQL."""

    from pathlib import Path

    from app.data.database import (
        DEFAULT_DATABASE_PATH,
        is_sqlite_uri,
        redact_database_uri,
    )
    from app.extensions import db
    from app.services.transfer_service import (
        TransferError,
        copy_sqlite_to_database,
    )
    from app.web.app import create_app

    uri = _database_uri()

    if is_sqlite_uri(uri):
        print(
            "CHYBA: DATABASE_URL musí mieť PostgreSQL (nastav ho v "
            ".env), cieľ nesmie byť SQLite."
        )
        sys.exit(1)

    source = Path(sqlite_path) if sqlite_path else Path(DEFAULT_DATABASE_PATH)

    if not source.exists():
        print(f"CHYBA: SQLite súbor '{source}' neexistuje.")
        sys.exit(1)

    print(f"Zdroj : {source}")
    print(f"Cieľ  : {redact_database_uri(uri)}")

    app = create_app()

    with app.app_context():
        try:
            copied = copy_sqlite_to_database(source, db.engine)
        except TransferError as error:
            print(f"CHYBA: {error}")
            sys.exit(1)

    print()
    print("Skopírované riadky:")

    for table, count in copied.items():
        print(f"  {table}: {count}")

    print()
    print("Hotovo. Pôvodný SQLite súbor ostal nezmenený (ponechaj si ho ako zálohu).")


def _run_audit_prune(days_text):
    """Zmaže záznamy zmien (audit log) staršie ako zadaný počet dní."""

    from app.extensions import db  # noqa: F401
    from app.services.audit_service import prune_older_than
    from app.web.app import create_app

    try:
        days = int(days_text)
    except (TypeError, ValueError):
        print("Použitie: python manage.py audit-prune <počet dní> (min. 30)")
        sys.exit(1)

    if days < 30:
        print("Z bezpečnostných dôvodov sa dajú zmazať len záznamy staršie "
              "ako 30 dní.")
        sys.exit(1)

    app = create_app()

    with app.app_context():
        removed = prune_older_than(days)

    print(f"Zmazaných záznamov starších ako {days} dní: {removed}")


def _run_serve():
    """Produkčný server (waitress) - bez debuggera, viac vlákien."""

    import os

    from app.web.app import _DEFAULT_SECRET_KEY, create_app

    secret_key = os.environ.get("SECRET_KEY", "")

    if not secret_key or secret_key == _DEFAULT_SECRET_KEY:
        print(
            "CHYBA: pre produkčný server musí byť nastavený vlastný "
            "SECRET_KEY (v .env alebo v prostredí). Vygeneruješ ho:\n"
            "  python -c \"import secrets; print(secrets.token_hex(32))\""
        )
        sys.exit(1)

    try:
        from waitress import serve
    except ImportError:
        print(
            "CHYBA: chýba balík 'waitress'. Nainštaluj závislosti: "
            "pip install -r requirements.txt"
        )
        sys.exit(1)

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    threads = int(os.environ.get("THREADS", "8"))

    behind_proxy = os.environ.get("BEHIND_PROXY", "0") == "1"
    secure_cookie = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    if behind_proxy and not secure_cookie:
        print(
            "UPOZORNENIE: BEHIND_PROXY=1, ale SESSION_COOKIE_SECURE nie "
            "je 1. Ak proxy poskytuje HTTPS, nastav "
            "SESSION_COOKIE_SECURE=1."
        )

    if host not in ("127.0.0.1", "localhost") and not behind_proxy:
        print(
            f"UPOZORNENIE: server počúva na {host} bez HTTPS. Heslá by "
            "sa po sieti posielali nešifrovane - použi reverzný proxy "
            "s HTTPS (pozri DEPLOYMENT.md) a HOST=127.0.0.1."
        )

    app = create_app()

    serve_options = {"host": host, "port": port, "threads": threads}

    if behind_proxy:
        # Waitress predvolene maže hlavičky X-Forwarded-* (aby ich nikto
        # nepodvrhol), takže by appka za proxy videla všetkých klientov
        # ako jednu adresu (a limit pokusov o prihlásenie by blokoval
        # všetkých naraz). Dôveryhodnému proxy (predvolene localhost,
        # inak TRUSTED_PROXY) ich výslovne povolíme; hlavičky od
        # čohokoľvek iného waitress naďalej zahodí.
        serve_options.update(
            trusted_proxy=os.environ.get("TRUSTED_PROXY", "127.0.0.1"),
            trusted_proxy_count=1,
            trusted_proxy_headers={
                "x-forwarded-for",
                "x-forwarded-proto",
                "x-forwarded-host",
            },
        )

    print(f"Server beží na http://{host}:{port} (vlákna: {threads}).")
    print("Zastavíš ho cez Ctrl+C.")

    serve(app, **serve_options)


def _run_create_user(username, role):
    """Vytvorí účet admina alebo manažéra (manažérovi sa pridelia
    pobočky, ktoré bude spravovať)."""

    import getpass

    from app.services.auth_service import create_admin, create_manager
    from app.services.branch_service import get_branches
    from app.web.app import create_app

    label = "administrátora" if role == "admin" else "manažéra"

    if not username:
        username = input(f"Používateľské meno pre {label}: ").strip()

    if not username:
        print("Používateľské meno nemôže byť prázdne.")
        return

    app = create_app()

    branch_ids = []

    if role == "manager":
        with app.app_context():
            branches = get_branches()

        if not branches:
            print(
                "Zatiaľ neexistuje žiadna pobočka. Spusti najprv "
                "'python manage.py migrate' (vytvorí predvolenú pobočku) "
                "alebo vytvor pobočku ako admin v appke."
            )
            return

        print("Dostupné pobočky:")

        for branch_id, name, active in branches:
            print(f"  {branch_id}: {name}{'' if active else ' (neaktívna)'}")

        raw = input(
            "ID pobočiek, ktoré bude manažér spravovať "
            "(oddelené čiarkou, napr. 1,2): "
        )

        try:
            branch_ids = [
                int(part) for part in raw.split(",") if part.strip()
            ]
        except ValueError:
            print("Neplatný zoznam - zadaj len čísla oddelené čiarkou.")
            return

        valid_ids = {branch[0] for branch in branches}

        if not branch_ids or not set(branch_ids) <= valid_ids:
            print("Zadaj aspoň jedno platné ID pobočky zo zoznamu vyššie.")
            return

    password = getpass.getpass("Heslo: ")

    if len(password) < 6:
        print("Heslo musí mať aspoň 6 znakov.")
        return

    password_confirm = getpass.getpass("Heslo znova: ")

    if password != password_confirm:
        print("Heslá sa nezhodujú.")
        return

    with app.app_context():
        if role == "admin":
            user_id = create_admin(username, password)
        else:
            user_id = create_manager(username, password, branch_ids)

    if user_id is None:
        print(f"Používateľské meno '{username}' už existuje.")
    else:
        print(f"Účet '{username}' ({role}) vytvorený.")


def _run_backup():
    from app.data.database import DEFAULT_DATABASE_PATH, is_sqlite_uri
    from app.services.backup_service import (
        BackupError,
        create_backup,
        create_postgres_backup,
    )

    uri = _database_uri()

    if not is_sqlite_uri(uri):
        try:
            backup_path = create_postgres_backup(uri, label="manualna")
        except BackupError as error:
            print(f"Záloha zlyhala: {error}")
            sys.exit(1)

        print(f"Záloha vytvorená: {backup_path}")
        return

    backup_path = create_backup(DEFAULT_DATABASE_PATH, label="manualna")

    if backup_path is None:
        print(
            "Databáza ešte neexistuje (žiadny súbor na zálohovanie) - "
            "spusti najprv 'python manage.py migrate'."
        )
    else:
        print(f"Záloha vytvorená: {backup_path}")


def _run_list_backups():
    from app.data.database import DEFAULT_DATABASE_PATH
    from app.services.backup_service import list_backups

    backups = list_backups(DEFAULT_DATABASE_PATH)

    if not backups:
        print("Žiadne zálohy zatiaľ neexistujú.")
        return

    print(f"Dostupné zálohy ({len(backups)}):\n")

    for backup_path, size_bytes, created_at in backups:
        size_kb = size_bytes / 1024
        timestamp = created_at.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {backup_path.name}  ({size_kb:.0f} KB, {timestamp})")


def _run_restore(backup_filename):
    from sqlalchemy.engine import make_url

    from app.data.database import DEFAULT_DATABASE_PATH, is_sqlite_uri
    from app.services.backup_service import (
        default_backups_dir,
        restore_backup,
    )

    uri = _database_uri()

    if not is_sqlite_uri(uri):
        # Obnova PostgreSQL prepíše celú databázu - radšej sa nerobí
        # automaticky, ale ručne a vedome.
        url = make_url(uri)
        backup_path = default_backups_dir() / backup_filename

        print("Obnova PostgreSQL databázy sa robí ručne (prepíše všetky dáta).")
        print("1. Zastav appku a urob čerstvú zálohu: python manage.py backup")
        print("2. Spusti (zadá sa heslo databázy):")
        print(
            f'   pg_restore --clean --if-exists --no-owner '
            f'--host {url.host or "localhost"} --port {url.port or 5432} '
            f'--username {url.username} --dbname {url.database} '
            f'"{backup_path}"'
        )
        return

    confirmation = input(
        f"Naozaj chceš prepísať aktuálnu databázu zálohou "
        f"'{backup_filename}'? Aktuálny stav sa pred tým tiež "
        f"zazálohuje. Napíš 'ano' pre potvrdenie: "
    )

    if confirmation.strip().lower() != "ano":
        print("Zrušené - nič sa nezmenilo.")
        return

    try:
        restore_backup(backup_filename, DEFAULT_DATABASE_PATH)
    except FileNotFoundError as error:
        print(str(error))
        print("Zoznam dostupných záloh: python manage.py list-backups")
        return

    print(f"Databáza obnovená zo zálohy '{backup_filename}'.")


def _run_migrate_postgres(uri):
    """Migrácia PostgreSQL: pred zmenou existujúcej databázy sa urobí
    záloha cez pg_dump (bez nej sa migrácia nespustí, ak to výslovne
    nepovolíš parametrom --no-backup)."""

    import sqlalchemy as sa
    from flask_migrate import stamp, upgrade

    from app.data.database import redact_database_uri
    from app.extensions import db
    from app.services.backup_service import (
        BackupError,
        create_postgres_backup,
    )
    from app.web.app import create_app

    print(f"Databáza: {redact_database_uri(uri)}")

    app = create_app()

    with app.app_context():
        existing_tables = set(sa.inspect(db.engine).get_table_names())

        if existing_tables and "--no-backup" not in sys.argv:
            try:
                backup_path = create_postgres_backup(
                    uri, label="pred-migraciou"
                )
            except BackupError as error:
                print(f"Záloha pred migráciou zlyhala: {error}")
                print("Migrácia sa NESPUSTILA (bezpečnosť dát).")
                print(
                    "Nainštaluj klientske nástroje PostgreSQL (pg_dump), "
                    "alebo na vlastné riziko spusti: "
                    "python manage.py migrate --no-backup"
                )
                sys.exit(1)

            print(f"Automatická záloha pred migráciou: {backup_path}")

        if "alembic_version" in existing_tables:
            upgrade()
            print("Databáza aktualizovaná (Alembic upgrade).")

        elif "employees" in existing_tables:
            stamp(revision=_get_base_revision(app))
            upgrade()
            print(
                "Existujúca databáza označená na pôvodnú schému a "
                "doplnená o novšie migrácie (Alembic stamp + upgrade)."
            )

        else:
            upgrade()
            print("Databáza vytvorená od začiatku (Alembic upgrade).")


def _run_migrate():
    import sqlalchemy as sa
    from pathlib import Path

    from flask_migrate import stamp, upgrade

    from app.data.database import DEFAULT_DATABASE_PATH, is_sqlite_uri

    uri = _database_uri()

    if not is_sqlite_uri(uri):
        _run_migrate_postgres(uri)
        return

    from app.extensions import db
    from app.services.backup_service import create_backup
    from app.web.app import create_app

    db_path = Path(DEFAULT_DATABASE_PATH)

    # Dôležité: zisťujeme toto PRED čímkoľvek iným, čo by mohlo súbor
    # vytvoriť (napr. samotné pripojenie cez SQLAlchemy nižšie súbor
    # automaticky založí, keby ešte neexistoval).
    file_existed_before = db_path.exists()

    print(f"Databázový súbor: {db_path}")

    backup_path = create_backup(DEFAULT_DATABASE_PATH, label="pred-migraciou")

    if backup_path is not None:
        print(f"Automatická záloha pred migráciou: {backup_path}")

    app = create_app()

    with app.app_context():
        inspector = sa.inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        if "alembic_version" in existing_tables:
            # Databáza je už spravovaná Alembicom - normálna
            # aktualizácia na najnovšiu migráciu.
            upgrade()
            print("Databáza aktualizovaná (Alembic upgrade).")

        elif "employees" in existing_tables:
            # Existujúca databáza zo staršej verzie appky (ručný
            # migračný runner pred prechodom na Alembic). Jej schéma
            # zodpovedá PRVEJ Alembic migrácii (tá bola vygenerovaná
            # presne podľa toho, čo ručný runner vytváral) - preto ju
            # označíme na TÚTO konkrétnu revíziu (nie na najnovšiu!)
            # a hneď potom normálne upgradneme, aby sa aplikovali aj
            # prípadné novšie migrácie (napr. pridanie absencií).
            # Stamp na najnovšiu revíziu by bol chybný - databáza by
            # sa tvárila ako aktuálna, no chýbali by jej stĺpce/
            # tabuľky z novších migrácií.
            base_revision = _get_base_revision(app)
            stamp(revision=base_revision)
            upgrade()
            print(
                "Existujúca databáza označená na pôvodnú schému a "
                "doplnená o novšie migrácie (Alembic stamp + upgrade) "
                "- žiadne pôvodné dáta sa nezmenili."
            )

        else:
            # Žiadne tabuľky - buď úplne nová inštalácia (súbor predtým
            # vôbec neexistoval - bežné, netreba sa pýtať), alebo
            # podozrivo PRÁZDNY súbor, ktorý už existoval. Druhý
            # prípad môže znamenať, že appka mieri na nesprávny/
            # poškodený súbor, zatiaľ čo tvoje reálne dáta sú inde -
            # preto to vyžaduje explicitné potvrdenie.
            if file_existed_before:
                print()
                print("=" * 70)
                print("UPOZORNENIE: Súbor databázy už existoval, ale je")
                print("úplne PRÁZDNY (žiadne tabuľky). Toto nie je bežný")
                print("stav pre existujúcu inštaláciu appky.")
                print()
                print("Ak si očakával, že tu budú tvoje dáta (zamestnanci,")
                print("zmeny...), ZASTAV a over si:")
                print("  - či appka mieri na správny súbor (cesta vyššie),")
                print("  - či nemáš zálohu inde na disku (napr. priečinok")
                print("    Downloads, alebo app/data/backups/) - over cez")
                print("    'python manage.py list-backups'.")
                print("=" * 70)

                confirmation = input(
                    "Naozaj chceš pokračovať a vytvoriť v tomto súbore "
                    "úplne novú, prázdnu databázu? Napíš 'ano' pre "
                    "potvrdenie: "
                )

                if confirmation.strip().lower() != "ano":
                    print("Zrušené - nič sa nevytvorilo/nezmenilo.")
                    return

            upgrade()
            print("Databáza vytvorená od začiatku (Alembic upgrade).")


def _get_base_revision(app):
    """Vráti ID prvej (základnej) Alembic migrácie."""

    from alembic.script import ScriptDirectory

    migrate_extension = app.extensions["migrate"]
    config = migrate_extension.migrate.get_config()
    script = ScriptDirectory.from_config(config)
    bases = script.get_bases()

    return bases[0]


if __name__ == "__main__":
    main()
