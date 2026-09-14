"""Služby pre prácu so smenami."""

from app.data.database import get_connection


def add_shift(
    employee_id,
    shift_date,
    start_time,
    end_time,
    shift_type,
):
    """Pridá smenu konkrétnemu zamestnancovi."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO shifts (
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type,
        ),
    )

    connection.commit()
    shift_id = cursor.lastrowid
    connection.close()

    return shift_id


def get_shifts():
    """Načíta všetky smeny spolu so zamestnancom."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            shifts.id,
            employees.first_name,
            employees.last_name,
            shifts.shift_date,
            shifts.start_time,
            shifts.end_time,
            shifts.shift_type
        FROM shifts
        JOIN employees
            ON shifts.employee_id = employees.id
        ORDER BY shifts.shift_date, shifts.start_time
        """
    )

    shifts = cursor.fetchall()
    connection.close()

    return shifts


def get_shift(shift_id):
    """Načíta jednu konkrétnu smenu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type
        FROM shifts
        WHERE id = ?
        """,
        (shift_id,),
    )

    shift = cursor.fetchone()
    connection.close()

    return shift


def update_shift(
    shift_id,
    employee_id,
    shift_date,
    start_time,
    end_time,
    shift_type,
):
    """Upraví existujúcu smenu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE shifts
        SET
            employee_id = ?,
            shift_date = ?,
            start_time = ?,
            end_time = ?,
            shift_type = ?
        WHERE id = ?
        """,
        (
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type,
            shift_id,
        ),
    )

    connection.commit()
    connection.close()


def delete_shift(shift_id):
    """Vymaže existujúcu smenu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM shifts
        WHERE id = ?
        """,
        (shift_id,),
    )

    connection.commit()
    connection.close()
