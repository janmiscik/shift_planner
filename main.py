"""Hlavný vstupný bod aplikácie Shift Planner."""

from app.data.database import create_tables
from app.services.employee_service import get_employees


def main():
    print("=" * 40)
    print("       SHIFT PLANNER")
    print("=" * 40)

    create_tables()

    employees = get_employees()

    print()
    print("ZAMESTNANCI V DATABÁZE")

    for employee in employees:
        employee_id = employee[0]
        first_name = employee[1]
        last_name = employee[2]
        position = employee[3]
        employment_type = employee[4]

        print(
            f"{employee_id}. "
            f"{first_name} {last_name} - "
            f"{position} - "
            f"{employment_type}"
        )


if __name__ == "__main__":
    main()