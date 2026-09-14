"""Hlavný vstupný bod aplikácie Shift Planner."""

from app.data.database import create_tables
from app.services.employee_service import get_employees
from app.services.shift_service import add_shift, get_shifts


def main():
    print("=" * 40)
    print("       SHIFT PLANNER")
    print("=" * 40)

    create_tables()

    employees = get_employees()

    if not employees:
        print("V databáze nie sú žiadni zamestnanci.")
        return

    employee = employees[0]

    shift_id = add_shift(
        employee_id=employee[0],
        shift_date="2026-09-15",
        start_time="06:00",
        end_time="14:00",
        shift_type="Ranná",
    )

    print()
    print("SMENA ULOŽENÁ")
    print(f"ID smeny: {shift_id}")
    print(
        f"Zamestnanec: "
        f"{employee[1]} {employee[2]}"
    )

    print()
    print("SMENY V DATABÁZE")

    shifts = get_shifts()

    for shift in shifts:
        print(
            f"{shift[0]}. "
            f"{shift[1]} {shift[2]} | "
            f"{shift[3]} | "
            f"{shift[4]} - {shift[5]} | "
            f"{shift[6]}"
        )


if __name__ == "__main__":
    main()