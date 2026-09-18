"""Služby pre prácu s oddeleniami."""

from app.data.database import get_connection


def department_name_exists(name, exclude_department_id=None):
    """Overí, či už existuje oddelenie s daným názvom (bez ohľadu na
    veľkosť písmen)."""

    connection = get_connection()
    cursor = connection.cursor()

    query = "SELECT id FROM departments WHERE LOWER(name) = LOWER(?)"
    params = [name]

    if exclude_department_id is not None:
        query += " AND id != ?"
        params.append(exclude_department_id)

    cursor.execute(query, tuple(params))
    exists = cursor.fetchone() is not None

    connection.close()

    return exists


def add_department(name):
    """Pridá nové oddelenie.

    Vráti ``None``, ak už oddelenie s týmto názvom existuje (namiesto
    pádu na UNIQUE obmedzení v databáze).
    """

    if department_name_exists(name):
        return None

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
    """Upraví názov oddelenia.

    Vráti ``False``, ak už iné oddelenie s týmto názvom existuje.
    """

    if department_name_exists(name, exclude_department_id=department_id):
        return False

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

    return True


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
