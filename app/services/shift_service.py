"""Služby pre prácu so smenami."""

from app.data.database import get_connection


def has_shift_collision(
    employee_id,
    shift_date,
    start_time,
    end_time,
    exclude_shift_id=None,
):
    """Overí, či sa smena prekrýva s existujúcou smenou."""

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT id
        FROM shifts
        WHERE employee_id = ?
          AND shift_date = ?
          AND (? < end_time AND ? > start_time)
    """

    params = [
        employee_id,
        shift_date,
        start_time,
        end_time,
    ]

    if exclude_shift_id is not None:
        query += """
            AND id != ?
        """
        params.append(exclude_shift_id)

    cursor.execute(
        query,
        tuple(params),
    )

    collision = cursor.fetchone()

    connection.close()

    return collision is not None


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


def get_shifts_by_employee(employee_id):
    """Načíta smeny konkrétneho zamestnanca."""

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
        WHERE shifts.employee_id = ?
        ORDER BY shifts.shift_date, shifts.start_time
        """,
        (employee_id,),
    )

    shifts = cursor.fetchall()
    connection.close()

    return shifts