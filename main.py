"""Hlavný vstupný bod aplikácie Shift Planner."""

from datetime import date, time

from app.models.employee import Employee
from app.models.shift import Shift


def main():
    print("=" * 40)
    print("       SHIFT PLANNER")
    print("=" * 40)

    employee = Employee(
        first_name="Ján",
        last_name="Novák",
        position="Skladník",
    )

    shift = Shift(
        shift_date=date(2026, 9, 15),
        start_time=time(6, 0),
        end_time=time(14, 0),
        shift_type="Ranná",
    )

    print()
    print("ZAMESTNANEC")
    print(f"Meno: {employee.full_name}")
    print(f"Pozícia: {employee.position}")

    print()
    print("SMENA")
    print(shift)


if __name__ == "__main__":
    main()