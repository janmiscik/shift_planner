"""Služby pre prácu so smenami.

Okrem CRUD operácií obsahuje aj validáciu biznis pravidiel pred
uložením smeny (``validate_shift``):
  - začiatok a koniec smeny sa nesmú zhodovať,
  - smena sa nesmie prekrývať s inou smenou toho istého zamestnanca
    (vrátane nočných smien, ktoré prechádzajú cez polnoc),
  - súčet hodín v danom kalendárnom týždni nesmie prekročiť
    zamestnancov týždenný pracovný fond (``employees.weekly_hours``),
  - zamestnanca je možné priradiť len na oddelenie, ku ktorému má
    aktívnu väzbu (``employee_departments``).

Nočné smeny (napr. 22:00 - 06:00): ak je čas konca menší alebo rovný
času začiatku, smena sa považuje za prechádzajúcu do nasledujúceho
dňa. Smena je v databáze evidovaná pod dátumom svojho ZAČIATKU.
"""

from datetime import datetime, timedelta

from app.data.database import get_connection


def _to_minutes(time_str):
    hours, minutes = time_str.split(":")[:2]
    return int(hours) * 60 + int(minutes)


def shift_duration_hours(start_time, end_time):
    """Vráti dĺžku smeny v hodinách (desatinné číslo).

    Ak je koniec skorší alebo rovnaký ako začiatok, smena sa počíta
    ako prechádzajúca cez polnoc (napr. 22:00 - 06:00 = 8 hodín).
    """

    duration_minutes = (
        _to_minutes(end_time) - _to_minutes(start_time)
    ) % (24 * 60)

    if duration_minutes == 0:
        # Rovnaký začiatok aj koniec - v praxi neplatný vstup, ktorý
        # odchytáva validate_shift(); tu sa netreba správať ako 24 h.
        duration_minutes = 0

    return duration_minutes / 60


def _shift_datetime_range(shift_date, start_time, end_time):
    """Vráti (začiatok, koniec) smeny ako skutočné ``datetime`` objekty.

    Ak je koniec skorší/rovnaký ako začiatok, koniec sa posunie na
    nasledujúci deň (nočná smena).
    """

    base_date = datetime.strptime(shift_date, "%Y-%m-%d")

    start_hour, start_minute = (int(part) for part in start_time.split(":")[:2])
    end_hour, end_minute = (int(part) for part in end_time.split(":")[:2])

    start_dt = base_date.replace(hour=start_hour, minute=start_minute)
    end_dt = base_date.replace(hour=end_hour, minute=end_minute)

    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


def week_bounds(shift_date):
    """Vráti (pondelok, nedeľa) kalendárneho týždňa pre daný dátum."""

    parsed = datetime.strptime(shift_date, "%Y-%m-%d").date()
    monday = parsed - timedelta(days=parsed.weekday())
    sunday = monday + timedelta(days=6)

    return monday.isoformat(), sunday.isoformat()


def has_shift_collision(
    employee_id,
    shift_date,
    start_time,
    end_time,
    exclude_shift_id=None,
):
    """Overí, či sa smena prekrýva s existujúcou smenou.

    Kontroluje aj prekrytie cez polnoc - preto sa najprv načítajú
    zamestnancove smeny v okolí daného dátumu (deň predtým a deň
    potom) a prekrytie sa vyhodnotí na skutočných ``datetime``
    intervaloch, nie len porovnaním textových časov v rámci
    jedného dňa.
    """

    new_start, new_end = _shift_datetime_range(
        shift_date, start_time, end_time
    )

    window_start = (new_start - timedelta(days=1)).date().isoformat()
    window_end = (new_end + timedelta(days=1)).date().isoformat()

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT id, shift_date, start_time, end_time
        FROM shifts
        WHERE employee_id = ?
          AND shift_date BETWEEN ? AND ?
    """

    params = [employee_id, window_start, window_end]

    if exclude_shift_id is not None:
        query += " AND id != ?"
        params.append(exclude_shift_id)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    connection.close()

    for row in rows:
        existing_start, existing_end = _shift_datetime_range(
            row[1], row[2], row[3]
        )

        if new_start < existing_end and new_end > existing_start:
            return True

    return False


def get_scheduled_hours_in_week(
    employee_id,
    shift_date,
    exclude_shift_id=None,
):
    """Súčet hodín naplánovaných zamestnancovi v týždni daného dátumu."""

    monday, sunday = week_bounds(shift_date)

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT start_time, end_time
        FROM shifts
        WHERE employee_id = ?
          AND shift_date BETWEEN ? AND ?
    """

    params = [employee_id, monday, sunday]

    if exclude_shift_id is not None:
        query += " AND id != ?"
        params.append(exclude_shift_id)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    connection.close()

    return sum(
        shift_duration_hours(row[0], row[1])
        for row in rows
    )


def validate_shift(
    employee_id,
    shift_date,
    start_time,
    end_time,
    department_id=None,
    exclude_shift_id=None,
):
    """Overí biznis pravidlá pre smenu a vráti zoznam chybových hlášok.

    Prázdny zoznam znamená, že smenu je možné uložiť.
    """

    from app.services.employee_service import get_employee
    from app.services.employee_department_service import (
        get_employee_department_ids,
    )

    errors = []

    if not start_time or not end_time or start_time == end_time:
        errors.append(
            "Začiatok a koniec smeny sa nemôžu zhodovať."
        )
        # Ostatné kontroly pri neplatnom čase nemajú zmysel.
        return errors

    if has_shift_collision(
        employee_id,
        shift_date,
        start_time,
        end_time,
        exclude_shift_id=exclude_shift_id,
    ):
        errors.append(
            "Zamestnanec už má v tomto čase inú smenu."
        )

    employee = get_employee(employee_id)

    if employee is not None and employee[4] is not None:
        weekly_limit = float(employee[4])

        already_scheduled = get_scheduled_hours_in_week(
            employee_id,
            shift_date,
            exclude_shift_id=exclude_shift_id,
        )

        new_duration = shift_duration_hours(start_time, end_time)

        if already_scheduled + new_duration > weekly_limit:
            errors.append(
                "Smena prekračuje týždenný pracovný fond zamestnanca "
                f"({already_scheduled + new_duration:.1f} h "
                f"z {weekly_limit:.1f} h)."
            )

    if department_id:
        allowed_department_ids = get_employee_department_ids(
            employee_id
        )

        if int(department_id) not in allowed_department_ids:
            errors.append(
                "Zamestnanec nie je priradený k vybranému oddeleniu."
            )

    return errors


def remove_duplicate_shifts():
    """Odstráni duplicitné smeny (rovnaký zamestnanec, dátum, čas aj
    typ), ponechá vždy len najstarší záznam.

    Slúži na jednorazové vyčistenie dát, ktoré mohli vzniknúť starou
    chybou v ``main.py`` (vkladal ukážkovú smenu bez kontroly pri
    každom spustení).
    """

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
        ORDER BY id
        """
    )

    seen = {}
    duplicate_ids = []

    for row in cursor.fetchall():
        shift_id = row[0]
        key = row[1:]

        if key in seen:
            duplicate_ids.append(shift_id)
        else:
            seen[key] = shift_id

    for shift_id in duplicate_ids:
        cursor.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))

    connection.commit()
    connection.close()

    return duplicate_ids


def find_overlapping_shifts():
    """Nájde smeny, ktoré sa prekrývajú (rovnaký zamestnanec, prekryv
    časov), aj keď nie sú úplne identické - napr. staré/testovacie
    dáta vložené mimo webového formulára (ktorý prekrytie nedovolí).

    Nič nemaže - len vráti konfliktné dvojice, aby si sa vedel
    rozhodnúť, ktorú smenu ponechať (zmazať vieš cez /shifts v appke).
    """

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            shifts.id,
            employees.first_name,
            employees.last_name,
            shifts.employee_id,
            shifts.shift_date,
            shifts.start_time,
            shifts.end_time
        FROM shifts
        JOIN employees ON shifts.employee_id = employees.id
        ORDER BY shifts.employee_id, shifts.shift_date, shifts.start_time
        """
    )

    rows = cursor.fetchall()
    connection.close()

    conflicts = []

    for i, row in enumerate(rows):
        row_start, row_end = _shift_datetime_range(
            row[4], row[5], row[6]
        )

        for other in rows[i + 1:]:
            if row[3] != other[3]:
                # Iný zamestnanec - prekrytie nás nezaujíma.
                continue

            other_start, other_end = _shift_datetime_range(
                other[4], other[5], other[6]
            )

            if row_start < other_end and row_end > other_start:
                conflicts.append((row, other))

    return conflicts


def add_shift(
    employee_id,
    shift_date,
    start_time,
    end_time,
    shift_type,
    department_id=None,
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
            shift_type,
            department_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type,
            department_id or None,
        ),
    )

    connection.commit()
    shift_id = cursor.lastrowid
    connection.close()

    return shift_id


def _shift_select(where_clause="", order_clause=""):
    return f"""
        SELECT
            shifts.id,
            employees.first_name,
            employees.last_name,
            shifts.shift_date,
            shifts.start_time,
            shifts.end_time,
            shifts.shift_type,
            shifts.employee_id,
            shifts.department_id,
            departments.name
        FROM shifts
        JOIN employees
            ON shifts.employee_id = employees.id
        LEFT JOIN departments
            ON shifts.department_id = departments.id
        {where_clause}
        {order_clause}
    """


def get_shifts():
    """Načíta všetky smeny spolu so zamestnancom a oddelením."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        _shift_select(
            order_clause="ORDER BY shifts.shift_date, shifts.start_time"
        )
    )

    shifts = cursor.fetchall()
    connection.close()

    return shifts


def get_shifts_in_range(start_date, end_date):
    """Načíta smeny v danom dátumovom rozsahu (vrátane)."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        _shift_select(
            where_clause="WHERE shifts.shift_date BETWEEN ? AND ?",
            order_clause="ORDER BY shifts.shift_date, shifts.start_time",
        ),
        (start_date, end_date),
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
            shift_type,
            department_id
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
    department_id=None,
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
            shift_type = ?,
            department_id = ?
        WHERE id = ?
        """,
        (
            employee_id,
            shift_date,
            start_time,
            end_time,
            shift_type,
            department_id or None,
            shift_id,
        ),
    )

    connection.commit()
    connection.close()


def move_shift(shift_id, shift_date, start_time, end_time):
    """Presunie smenu na iný dátum/čas (napr. drag-and-drop v kalendári)."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE shifts
        SET shift_date = ?, start_time = ?, end_time = ?
        WHERE id = ?
        """,
        (shift_date, start_time, end_time, shift_id),
    )

    connection.commit()
    connection.close()


def delete_shift(shift_id):
    """Vymaže existujúcu smenu."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "DELETE FROM shifts WHERE id = ?",
        (shift_id,),
    )

    connection.commit()
    connection.close()


def get_shifts_by_employee(employee_id):
    """Načíta smeny konkrétneho zamestnanca."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        _shift_select(
            where_clause="WHERE shifts.employee_id = ?",
            order_clause="ORDER BY shifts.shift_date, shifts.start_time",
        ),
        (employee_id,),
    )

    shifts = cursor.fetchall()
    connection.close()

    return shifts
