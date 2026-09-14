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
    """Načíta všetkých zamestnancov."""

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


def get_employee(employee_id):
    """Načíta jedného zamestnanca."""

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
        WHERE id = ?
        """,
        (employee_id,),
    )

    employee = cursor.fetchone()
    connection.close()

    return employee


def update_employee(
    employee_id,
    first_name,
    last_name,
    position,
    employment_type,
):
    """Upraví údaje zamestnanca."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE employees
        SET
            first_name = ?,
            last_name = ?,
            position = ?,
            employment_type = ?
        WHERE id = ?
        """,
        (
            first_name,
            last_name,
            position,
            employment_type,
            employee_id,
        ),
    )

    connection.commit()
    connection.close()


def set_employee_active(employee_id, active):
    """Aktivuje alebo deaktivuje zamestnanca."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE employees
        SET active = ?
        WHERE id = ?
        """,
        (
            active,
            employee_id,
        ),
    )

    connection.commit()
    connection.close()

