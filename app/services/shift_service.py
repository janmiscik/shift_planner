"""Služby pre prácu so smenami.

Okrem CRUD operácií obsahuje aj validáciu biznis pravidiel pred
uložením smeny (``validate_shift``):
  - začiatok a koniec smeny sa nesmú zhodovať,
  - smena sa nesmie prekrývať s inou smenou toho istého zamestnanca
    (vrátane nočných smien, ktoré prechádzajú cez polnoc),
  - medzi dvomi po sebe idúcimi smenami musí byť minimálny odpočinok
    (``MIN_REST_HOURS``, Zákonník práce),
  - súčet hodín v danom kalendárnom týždni nesmie prekročiť
    zamestnancov týždenný pracovný fond (``employees.weekly_hours``),
  - súčet hodín v týždni nesmie prekročiť zákonný strop vrátane
    nadčasov (``MAX_WEEKLY_HOURS_WITH_OVERTIME``, Zákonník práce),
  - zamestnanca je možné priradiť len na oddelenie, ku ktorému má
    aktívnu väzbu (``employee_departments``),
  - zohľadňujú sa schválené absencie.

Nočné smeny (napr. 22:00 - 06:00): ak je čas konca menší alebo rovný
času začiatku, smena sa považuje za prechádzajúcu do nasledujúceho
dňa. Smena je v databáze evidovaná pod dátumom svojho ZAČIATKU.
"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.extensions import db
from app.orm_models import Department, Employee, Shift

# Minimálny odpočinok medzi dvomi po sebe idúcimi smenami (hodiny).
# Zákonník práce (§ 92) vyžaduje spravidla 12 hodín nepretržitého
# odpočinku počas 24 hodín (v niektorých prevádzkach/dohodou možno
# skrátiť až na 8 h s náhradným odpočinkom) - preto je to
# nastaviteľné cez premennú prostredia, predvolene 12.
MIN_REST_HOURS = float(os.environ.get("MIN_REST_HOURS", 12))

# Zákonný strop týždenného pracovného času vrátane nadčasov
# (Zákonník práce § 97 - priemerne 48 h týždenne).
MAX_WEEKLY_HOURS_WITH_OVERTIME = float(
    os.environ.get("MAX_WEEKLY_HOURS_WITH_OVERTIME", 48)
)


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

    return duration_minutes / 60


def _shift_datetime_range(shift_date, start_time, end_time):
    """Vráti (začiatok, koniec) smeny ako skutočné ``datetime`` objekty.

    Ak je koniec skorší/rovnaký ako začiatok, koniec sa posunie na
    nasledujúci deň (nočná smena).
    """

    base_date = datetime.strptime(shift_date, "%Y-%m-%d")

    start_hour, start_minute = (
        int(part) for part in start_time.split(":")[:2]
    )
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

    query = select(
        Shift.id, Shift.shift_date, Shift.start_time, Shift.end_time
    ).where(
        Shift.employee_id == employee_id,
        Shift.shift_date.between(window_start, window_end),
    )

    if exclude_shift_id is not None:
        query = query.where(Shift.id != exclude_shift_id)

    rows = db.session.execute(query).all()

    for row in rows:
        existing_start, existing_end = _shift_datetime_range(
            row[1], row[2], row[3]
        )

        if new_start < existing_end and new_end > existing_start:
            return True

    return False


def get_adjacent_shift_gaps(
    employee_id,
    shift_date,
    start_time,
    end_time,
    exclude_shift_id=None,
):
    """Vráti (odpočinok_pred, odpočinok_po) v hodinách - medzeru medzi
    touto smenou a najbližšou predchádzajúcou/nasledujúcou smenou
    toho istého zamestnanca.

    Pre stranu, kde žiadna susedná smena nie je (napr. úplne prvá
    naplánovaná smena), vráti na danej pozícii ``None``.
    """

    new_start, new_end = _shift_datetime_range(
        shift_date, start_time, end_time
    )

    window_start = (new_start - timedelta(days=3)).date().isoformat()
    window_end = (new_end + timedelta(days=3)).date().isoformat()

    query = select(
        Shift.id, Shift.shift_date, Shift.start_time, Shift.end_time
    ).where(
        Shift.employee_id == employee_id,
        Shift.shift_date.between(window_start, window_end),
    )

    if exclude_shift_id is not None:
        query = query.where(Shift.id != exclude_shift_id)

    rows = db.session.execute(query).all()

    rest_before = None
    rest_after = None

    for row in rows:
        existing_start, existing_end = _shift_datetime_range(
            row[1], row[2], row[3]
        )

        if existing_end <= new_start:
            gap = (new_start - existing_end).total_seconds() / 3600

            if rest_before is None or gap < rest_before:
                rest_before = gap

        elif existing_start >= new_end:
            gap = (existing_start - new_end).total_seconds() / 3600

            if rest_after is None or gap < rest_after:
                rest_after = gap

    return rest_before, rest_after


def get_scheduled_hours_in_week(
    employee_id,
    shift_date,
    exclude_shift_id=None,
):
    """Súčet hodín naplánovaných zamestnancovi v týždni daného dátumu."""

    monday, sunday = week_bounds(shift_date)

    query = select(Shift.start_time, Shift.end_time).where(
        Shift.employee_id == employee_id,
        Shift.shift_date.between(monday, sunday),
    )

    if exclude_shift_id is not None:
        query = query.where(Shift.id != exclude_shift_id)

    rows = db.session.execute(query).all()

    return sum(shift_duration_hours(row[0], row[1]) for row in rows)


def get_employee_weekly_hours_status(employee_id, shift_date):
    """Vráti stav týždenného fondu hodín zamestnanca pre týždeň,
    v ktorom leží ``shift_date``.

    Vráti ``None``, ak zamestnanec nemá nastavený týždenný fond
    hodín (``employees.weekly_hours`` je prázdne). Inak vráti
    slovník s hodinami odpracovanými/naplánovanými v danom týždni,
    fondom a stavom:
      - "ok"   - do 80 % fondu,
      - "near" - 80 - 100 % fondu (blíži sa k limitu),
      - "over" - nad 100 % fondu (limit je prekročený).
    """

    from app.services.employee_service import get_employee

    employee = get_employee(employee_id)

    if employee is None or employee[4] is None:
        return None

    limit = float(employee[4])
    used = get_scheduled_hours_in_week(employee_id, shift_date)
    percent = (used / limit * 100) if limit else 0

    if percent > 100:
        status = "over"
    elif percent >= 80:
        status = "near"
    else:
        status = "ok"

    return {
        "used": round(used, 1),
        "limit": round(limit, 1),
        "percent": round(percent, 1),
        "status": status,
    }


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
    from app.services.absence_service import get_employee_absence_on_date

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

    rest_before, rest_after = get_adjacent_shift_gaps(
        employee_id,
        shift_date,
        start_time,
        end_time,
        exclude_shift_id=exclude_shift_id,
    )

    if rest_before is not None and rest_before < MIN_REST_HOURS:
        errors.append(
            f"Medzi touto a predchádzajúcou smenou zamestnanca je len "
            f"{rest_before:.1f} h odpočinku (Zákonník práce vyžaduje "
            f"aspoň {MIN_REST_HOURS:.0f} h)."
        )

    if rest_after is not None and rest_after < MIN_REST_HOURS:
        errors.append(
            f"Medzi touto a nasledujúcou smenou zamestnanca je len "
            f"{rest_after:.1f} h odpočinku (Zákonník práce vyžaduje "
            f"aspoň {MIN_REST_HOURS:.0f} h)."
        )

    absence = get_employee_absence_on_date(employee_id, shift_date)

    if absence is not None:
        errors.append(
            f"Zamestnanec má na tento deň schválenú neprítomnosť "
            f"({absence[1]})."
        )

    employee = get_employee(employee_id)

    new_duration = shift_duration_hours(start_time, end_time)
    already_scheduled = get_scheduled_hours_in_week(
        employee_id,
        shift_date,
        exclude_shift_id=exclude_shift_id,
    )

    if employee is not None and employee[4] is not None:
        weekly_limit = float(employee[4])

        if already_scheduled + new_duration > weekly_limit:
            errors.append(
                "Smena prekračuje týždenný pracovný fond zamestnanca "
                f"({already_scheduled + new_duration:.1f} h "
                f"z {weekly_limit:.1f} h)."
            )

    if already_scheduled + new_duration > MAX_WEEKLY_HOURS_WITH_OVERTIME:
        errors.append(
            "Smena prekračuje zákonný strop týždenného pracovného "
            "času vrátane nadčasov podľa Zákonníka práce "
            f"({already_scheduled + new_duration:.1f} h "
            f"z max. {MAX_WEEKLY_HOURS_WITH_OVERTIME:.0f} h)."
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

    query = select(
        Shift.id,
        Shift.employee_id,
        Shift.shift_date,
        Shift.start_time,
        Shift.end_time,
        Shift.shift_type,
    ).order_by(Shift.id)

    rows = db.session.execute(query).all()

    seen = {}
    duplicate_ids = []

    for row in rows:
        shift_id = row[0]
        key = tuple(row[1:])

        if key in seen:
            duplicate_ids.append(shift_id)
        else:
            seen[key] = shift_id

    if duplicate_ids:
        db.session.execute(sa_delete(Shift).where(Shift.id.in_(duplicate_ids)))
        db.session.commit()

    return duplicate_ids


def find_overlapping_shifts():
    """Nájde smeny, ktoré sa prekrývajú (rovnaký zamestnanec, prekryv
    časov), aj keď nie sú úplne identické - napr. staré/testovacie
    dáta vložené mimo webového formulára (ktorý prekrytie nedovolí).

    Nič nemaže - len vráti konfliktné dvojice, aby si sa vedel
    rozhodnúť, ktorú smenu ponechať (zmazať vieš cez /shifts v appke).
    """

    query = (
        select(
            Shift.id,
            Employee.first_name,
            Employee.last_name,
            Shift.employee_id,
            Shift.shift_date,
            Shift.start_time,
            Shift.end_time,
        )
        .join(Employee, Shift.employee_id == Employee.id)
        .order_by(Shift.employee_id, Shift.shift_date, Shift.start_time)
    )

    rows = db.session.execute(query).all()

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

    shift = Shift(
        employee_id=employee_id,
        shift_date=shift_date,
        start_time=start_time,
        end_time=end_time,
        shift_type=shift_type,
        department_id=department_id or None,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )

    db.session.add(shift)
    db.session.commit()

    return shift.id


def _shift_columns():
    return (
        Shift.id,
        Employee.first_name,
        Employee.last_name,
        Shift.shift_date,
        Shift.start_time,
        Shift.end_time,
        Shift.shift_type,
        Shift.employee_id,
        Shift.department_id,
        Department.name,
        Shift.created_at,
    )


def _shift_base_query():
    return (
        select(*_shift_columns())
        .join(Employee, Shift.employee_id == Employee.id)
        .outerjoin(Department, Shift.department_id == Department.id)
    )


def get_shifts():
    """Načíta všetky smeny spolu so zamestnancom a oddelením."""

    query = _shift_base_query().order_by(
        Shift.shift_date, Shift.start_time
    )

    return db.session.execute(query).all()


def get_shifts_in_range(start_date, end_date):
    """Načíta smeny v danom dátumovom rozsahu (vrátane)."""

    query = (
        _shift_base_query()
        .where(Shift.shift_date.between(start_date, end_date))
        .order_by(Shift.shift_date, Shift.start_time)
    )

    return db.session.execute(query).all()


def get_shift(shift_id):
    """Načíta jednu konkrétnu smenu."""

    query = select(
        Shift.id,
        Shift.employee_id,
        Shift.shift_date,
        Shift.start_time,
        Shift.end_time,
        Shift.shift_type,
        Shift.department_id,
    ).where(Shift.id == shift_id)

    return db.session.execute(query).first()


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

    shift = db.session.get(Shift, shift_id)

    if shift is None:
        return

    shift.employee_id = employee_id
    shift.shift_date = shift_date
    shift.start_time = start_time
    shift.end_time = end_time
    shift.shift_type = shift_type
    shift.department_id = department_id or None

    db.session.commit()


def move_shift(shift_id, shift_date, start_time, end_time):
    """Presunie smenu na iný dátum/čas (napr. drag-and-drop v kalendári)."""

    shift = db.session.get(Shift, shift_id)

    if shift is None:
        return

    shift.shift_date = shift_date
    shift.start_time = start_time
    shift.end_time = end_time

    db.session.commit()


def delete_shift(shift_id):
    """Vymaže existujúcu smenu."""

    shift = db.session.get(Shift, shift_id)

    if shift is None:
        return

    db.session.delete(shift)
    db.session.commit()


def filter_shifts_by_department(shifts, department_id):
    """Vyfiltruje smeny patriace k danému oddeleniu.

    Zohľadní dva prípady:
      1. smena má oddelenie nastavené priamo (``shifts.department_id``),
      2. smena oddelenie nastavené nemá (staršie dáta, alebo sa pri
         pridávaní nevyplnilo - je to nepovinné pole), ale zamestnanec
         je k danému oddeleniu priradený cez ``employee_departments``.
    """

    from app.services.employee_department_service import (
        get_employee_department_ids,
    )

    department_ids_cache = {}
    filtered = []

    for shift in shifts:
        shift_department_id = shift[8]
        employee_id = shift[7]

        if shift_department_id == department_id:
            filtered.append(shift)
            continue

        if shift_department_id is not None:
            # Smena má explicitne INÉ oddelenie - nepatrí sem.
            continue

        if employee_id not in department_ids_cache:
            department_ids_cache[employee_id] = get_employee_department_ids(
                employee_id
            )

        if department_id in department_ids_cache[employee_id]:
            filtered.append(shift)

    return filtered


def backfill_shift_departments():
    """Jednorazovo doplní ``department_id`` do starších smien, kde
    chýba, ale je to jednoznačné - zamestnanec má priradené presne
    jedno oddelenie.

    Vráti (počet_doplnených, počet_preskočených). Preskočené sú tie,
    kde zamestnanec nemá priradené žiadne alebo má priradené viac
    oddelení naraz (tam sa nedá bezpečne uhádnuť, ktoré je správne).
    """

    from app.services.employee_department_service import (
        get_employee_department_ids,
    )

    query = select(Shift.id, Shift.employee_id).where(
        Shift.department_id.is_(None)
    )
    rows = db.session.execute(query).all()

    department_ids_cache = {}
    updated = 0
    skipped = 0

    for shift_id, employee_id in rows:
        if employee_id not in department_ids_cache:
            department_ids_cache[employee_id] = get_employee_department_ids(
                employee_id
            )

        department_ids = department_ids_cache[employee_id]

        if len(department_ids) == 1:
            department_id = next(iter(department_ids))

            shift = db.session.get(Shift, shift_id)
            shift.department_id = department_id

            updated += 1
        else:
            skipped += 1

    db.session.commit()

    return updated, skipped


def get_shifts_by_employee(employee_id):
    """Načíta smeny konkrétneho zamestnanca."""

    query = (
        _shift_base_query()
        .where(Shift.employee_id == employee_id)
        .order_by(Shift.shift_date, Shift.start_time)
    )

    return db.session.execute(query).all()
