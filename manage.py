"""Správcovské príkazy pre Shift Planner.

Použitie:
    python manage.py migrate               # aplikuje databázové migrácie
                                            # (najprv automaticky zálohuje
                                            # existujúcu databázu)
    python manage.py runserver             # spustí vývojový webový server
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
        from app.web.app import create_app

        create_app().run(debug=True)

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


def _run_backup():
    from app.data.database import DEFAULT_DATABASE_PATH
    from app.services.backup_service import create_backup

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
    from app.data.database import DEFAULT_DATABASE_PATH
    from app.services.backup_service import restore_backup

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


def _run_migrate():
    import sqlalchemy as sa
    from flask_migrate import stamp, upgrade

    from app.data.database import DEFAULT_DATABASE_PATH
    from app.extensions import db
    from app.services.backup_service import create_backup
    from app.web.app import create_app

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
            # Úplne nová, prázdna databáza - vytvor všetky tabuľky.
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
