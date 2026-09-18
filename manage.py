"""Správcovské príkazy pre Shift Planner.

Použitie:
    python manage.py migrate             # aplikuje databázové migrácie
    python manage.py runserver           # spustí vývojový webový server
    python manage.py cleanup-duplicates  # zmaže duplicitné smeny
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

    else:
        print(f"Neznámy príkaz: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
