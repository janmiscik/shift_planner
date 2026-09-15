"""Služby pre priradenie zamestnancov k oddeleniam."""

from app.data.database import get_connection


def add_employee_department(
    employee_id,
    department_id,
    weekly_hours,
):
    """Priradí zamestnanca k oddeleniu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM employee_departments
        WHERE employee_id = ?
          AND department_id = ?
        """,
        (
            employee_id,
            department_id,
        ),
    )

    existing_assignment = cursor.fetchone()

    if existing_assignment:
        connection.close()
        return None

    cursor.execute(
        """
        INSERT INTO employee_departments (
            employee_id,
            department_id,
            weekly_hours
        )
        VALUES (?, ?, ?)
        """,
        (
            employee_id,
            department_id,
            weekly_hours,
        ),
    )

    connection.commit()
    assignment_id = cursor.lastrowid
    connection.close()

    return assignment_id


def get_employee_departments(employee_id):
    """Načíta oddelenia konkrétneho zamestnanca."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            employee_departments.id,
            departments.id,
            departments.name,
            employee_departments.weekly_hours
        FROM employee_departments
        JOIN departments
            ON employee_departments.department_id = departments.id
        WHERE employee_departments.employee_id = ?
        ORDER BY departments.name
        """,
        (employee_id,),
    )

    assignments = cursor.fetchall()
    connection.close()

    return assignments


def get_employee_department(
    assignment_id,
):
    """Načíta jedno priradenie."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            employee_id,
            department_id,
            weekly_hours
        FROM employee_departments
        WHERE id = ?
        """,
        (assignment_id,),
    )

    assignment = cursor.fetchone()
    connection.close()

    return assignment


def update_employee_department(
    assignment_id,
    department_id,
    weekly_hours,
):
    """Upraví priradenie zamestnanca."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE employee_departments
        SET
            department_id = ?,
            weekly_hours = ?
        WHERE id = ?
        """,
        (
            department_id,
            weekly_hours,
            assignment_id,
        ),
    )

    connection.commit()
    connection.close()


def delete_employee_department(
    assignment_id,
):
    """Odstráni priradenie zamestnanca k oddeleniu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM employee_departments
        WHERE id = ?
        """,
        (assignment_id,),
    )

    connection.commit()
    connection.close()


def get_employee_department_hours(
    employee_id,
):
    """Vráti celkový počet hodín podľa oddelení."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COALESCE(
                SUM(weekly_hours),
                0
            )
        FROM employee_departments
        WHERE employee_id = ?
        """,
        (employee_id,),
    )

    weekly_hours = cursor.fetchone()[0]
    connection.close()

    return weekly_hours