"""Hlavný vstupný bod aplikácie Shift Planner.

Poznámka: reálne rozhranie appky je webová appka
(``python manage.py runserver``). Tento skript slúži len na rýchly
prehľad stavu databázy z príkazového riadku a NEVKLADÁ žiadne
testovacie/ukážkové dáta - robil to skôr a spôsobovalo to duplicitné
smeny pri každom opätovnom spustení.
"""

import sqlalchemy as sa

from app.extensions import db
from app.services.employee_service import get_employees
from app.services.shift_service import get_shifts
from app.web.app import create_app


def main():
    print("=" * 40)
    print("       SHIFT PLANNER")
    print("=" * 40)

    app = create_app()

    with app.app_context():
        inspector = sa.inspect(db.engine)

        if "employees" not in inspector.get_table_names():
            print("Databáza ešte nie je pripravená.")
            print("Spusti najprv: python manage.py migrate")
            return

        employees = get_employees()

        if not employees:
            print("V databáze nie sú žiadni zamestnanci.")
            print(
                "Spusti webovú appku príkazom "
                "'python manage.py runserver' a pridaj ich tam."
            )
            return

        print(f"Zamestnancov v databáze: {len(employees)}")

        shifts = get_shifts()

        print(f"Smien v databáze: {len(shifts)}")
        print()

        for shift in shifts:
            print(
                f"{shift[0]}. "
                f"{shift[1]} {shift[2]} | "
                f"{shift[3]} | "
                f"{shift[4]} - {shift[5]} | "
                f"{shift[6]}"
            )

    print()
    print(
        "Pre pridávanie/úpravu smien spusti webovú appku: "
        "python manage.py runserver"
    )


if __name__ == "__main__":
    main()
