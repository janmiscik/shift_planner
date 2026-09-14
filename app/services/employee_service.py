"""Služby pre prácu so zamestnancami."""

from app.data.database import get_connection


def add_employee(
    first_name,
    last_name,
    position,
    employment_type="full-time",
):
    """Pridá zamestnanca do databázy."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO employees (
            first_name,
            last_name,
            position,
            employment_type
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            first_name,
            last_name,
            position,
            employment_type,
        ),
    )

    connection.commit()
    employee_id = cursor.lastrowid
    connection.close()

    return employee_id

def get_employees():
    """Načíta všetkých zamestnancov z databázy."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            first_name,
            last_name,
            position,
            employment_type,
            active
        FROM employees
        ORDER BY last_name, first_name
        """
    )

    employees = cursor.fetchall()
    connection.close()

    return employees