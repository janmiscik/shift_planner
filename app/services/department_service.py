"""Služby pre prácu s oddeleniami."""

from app.data.database import get_connection


def add_department(name):
    """Pridá nové oddelenie."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO departments (name)
        VALUES (?)
        """,
        (name,),
    )

    connection.commit()
    department_id = cursor.lastrowid
    connection.close()

    return department_id


def get_departments():
    """Načíta všetky oddelenia."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            active
        FROM departments
        ORDER BY name
        """
    )

    departments = cursor.fetchall()
    connection.close()

    return departments


def get_department(department_id):
    """Načíta jedno oddelenie."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            active
        FROM departments
        WHERE id = ?
        """,
        (department_id,),
    )

    department = cursor.fetchone()
    connection.close()

    return department


def update_department(department_id, name):
    """Upraví názov oddelenia."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE departments
        SET name = ?
        WHERE id = ?
        """,
        (
            name,
            department_id,
        ),
    )

    connection.commit()
    connection.close()


def set_department_active(department_id, active):
    """Aktivuje alebo deaktivuje oddelenie."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE departments
        SET active = ?
        WHERE id = ?
        """,
        (
            active,
            department_id,
        ),
    )

    connection.commit()
    connection.close()
