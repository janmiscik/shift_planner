"""Správcovské príkazy pre Shift Planner.

Použitie:
    python manage.py migrate             # aplikuje databázové migrácie
    python manage.py runserver           # spustí vývojový webový server
    python manage.py cleanup-duplicates  # zmaže PRESNE identické duplicity
    python manage.py find-overlaps       # nájde prekrývajúce sa smeny
                                          # (aj keď nie sú identické) -
                                          # nič nemaže, len vypíše
"""

import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "migrate":
        from app.data.migrations import run_migrations

        run_migrations()

    elif command == "runserver":
        from app.web.app import app, create_tables

        create_tables()
        app.run(debug=True)

    elif command == "cleanup-duplicates":
        from app.services.shift_service import remove_duplicate_shifts

        removed = remove_duplicate_shifts()

        if removed:
            print(f"Odstránených duplicitných smien: {len(removed)}")
            print(f"ID zmazaných smien: {removed}")
        else:
            print("Žiadne duplicitné smeny sa nenašli.")

    elif command == "find-overlaps":
        from app.services.shift_service import find_overlapping_shifts

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

    else:
        print(f"Neznámy príkaz: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
