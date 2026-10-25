"""Dostupnosť zamestnancov: kedy NEMÔŽU pracovať.

Zamestnanec si zapíše opakované (napr. "každú stredu 14:00-22:00 -
škola") alebo jednorazové (napr. "5.-7.10. celý deň - svadba")
obmedzenia. Nie je to absencia ani žiadosť - neschvaľuje sa. Manažér
dostane pri plánovaní UPOZORNENIE (pri pridaní/úprave/presune smeny, v
zozname smien, v kalendári, pri kopírovaní týždňa a výmenách), ale smenu
môže zapísať aj tak - ide o mäkké obmedzenie, nie o pravidlo, ktoré
smenu zablokuje.

Pravidlo koliduje so smenou, ak sa ich časy prekrývajú; smena cez
polnoc sa rozdelí na kalendárne dni, takže nočná z piatka na sobotu
koliduje aj s pravidlom "každú sobotu do 08:00".
"""

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.extensions import db
from app.orm_models import AvailabilityRule, Branch, Employee

MAX_RULES_PER_EMPLOYEE = 50

WEEKDAY_EVERY = [
    "Každý pondelok",
    "Každý utorok",
    "Každú stredu",
    "Každý štvrtok",
    "Každý piatok",
    "Každú sobotu",
    "Každú nedeľu",
]

_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _normalize_time(value):
    """``HH:MM`` (aj ``H:MM``) -> ``HH:MM``; neplatné/prázdne -> ``None``."""

    match = _TIME_PATTERN.match((value or "").strip())

    if not match:
        return None

    hour, minute = int(match.group(1)), int(match.group(2))

    if hour > 23 or minute > 59:
        return None

    return f"{hour:02d}:{minute:02d}"


def _date_text(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


# ---------------------------------------------------------------------
# Správa pravidiel
# ---------------------------------------------------------------------

def create_rule(
    employee_id,
    weekday=None,
    date_from=None,
    date_to=None,
    time_from=None,
    time_to=None,
    note=None,
    today=None,
):
    """Vytvorí pravidlo nedostupnosti. Vráti ``(id, None)`` alebo
    ``(None, chyba)``.

    Opakované: ``weekday`` 0-6 (platnosť ``date_from``/``date_to`` je
    nepovinná). Jednorazové: ``weekday=None`` a ``date_from`` povinný
    (``date_to`` predvolene rovnaký deň). Čas: obe hodnoty (od < do), alebo
    ani jedna = celý deň.
    """

    today = today or date.today()

    employee = db.session.get(Employee, employee_id)

    if employee is None:
        return None, "Zamestnanec neexistuje."

    start = _parse_date(date_from)
    end = _parse_date(date_to)

    if date_from and start is None or date_to and end is None:
        return None, "Zadaj platné dátumy."

    if weekday is not None:
        if weekday not in range(7):
            return None, "Vyber deň v týždni."

        if start and end and end < start:
            return None, "Koniec platnosti nemôže byť pred jej začiatkom."
    else:
        if start is None:
            return None, "Zadaj dátum."

        end = end or start

        if end < start:
            return None, "Koniec nemôže byť pred začiatkom."

    if end is not None and end < today:
        return None, "Obdobie už skončilo - zadaj dnešný alebo budúci dátum."

    has_from, has_to = bool((time_from or "").strip()), bool((time_to or "").strip())

    if has_from != has_to:
        return None, "Zadaj čas od aj do, alebo nechaj celý deň."

    normalized_from = normalized_to = None

    if has_from:
        normalized_from = _normalize_time(time_from)
        normalized_to = _normalize_time(time_to)

        if normalized_from is None or normalized_to is None:
            return None, "Zadaj platné časy."

        if normalized_to <= normalized_from:
            return None, (
                "Čas do musí byť neskôr než čas od (pre noc cez polnoc "
                "pridaj dve pravidlá)."
            )

    note = (note or "").strip() or None

    if note and len(note) > 255:
        return None, "Poznámka môže mať najviac 255 znakov."

    active = len(get_rule_rows(employee_id=employee_id, today=today))

    if active >= MAX_RULES_PER_EMPLOYEE:
        return None, f"Máš už {MAX_RULES_PER_EMPLOYEE} pravidiel - zruš nepotrebné."

    values = {
        "weekday": weekday,
        "date_from": start.isoformat() if start else None,
        "date_to": end.isoformat() if end else None,
        "time_from": normalized_from,
        "time_to": normalized_to,
    }

    duplicate = db.session.execute(
        select(AvailabilityRule.id).where(
            AvailabilityRule.employee_id == employee_id,
            *[
                getattr(AvailabilityRule, key).is_(None)
                if value is None
                else getattr(AvailabilityRule, key) == value
                for key, value in values.items()
            ],
        )
    ).first()

    if duplicate:
        return None, "Také pravidlo už máš zapísané."

    rule = AvailabilityRule(
        employee_id=employee_id, note=note, created_at=_now(), **values
    )

    db.session.add(rule)
    db.session.commit()

    return rule.id, None


def delete_rule(rule_id, employee_id=None):
    """Zmaže pravidlo; ak je ``employee_id`` zadané, musí patriť jemu.
    Vráti ``True``, ak sa zmazalo."""

    rule = db.session.get(AvailabilityRule, rule_id)

    if rule is None or (employee_id is not None and rule.employee_id != employee_id):
        return False

    db.session.delete(rule)
    db.session.commit()

    return True


def get_rule_rows(employee_id=None, branch_ids=None, include_expired=False, today=None):
    """Pravidlá ako ``(pravidlo, meno, priezvisko, branch_id, pobočka)``,
    zoradené podľa zamestnanca; opakované pred jednorazovými. Bez
    ``include_expired`` len tie, ktoré ešte neskončili."""

    today = today or date.today()

    query = (
        select(
            AvailabilityRule,
            Employee.first_name,
            Employee.last_name,
            Employee.branch_id,
            Branch.name,
        )
        .join(Employee, AvailabilityRule.employee_id == Employee.id)
        .join(Branch, Employee.branch_id == Branch.id)
    )

    if employee_id is not None:
        query = query.where(AvailabilityRule.employee_id == employee_id)

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    if not include_expired:
        query = query.where(
            (AvailabilityRule.date_to.is_(None))
            | (AvailabilityRule.date_to >= today.isoformat())
        )

    rows = db.session.execute(query).all()

    def sort_key(row):
        rule = row[0]

        return (
            row[2].casefold(),
            row[1].casefold(),
            0 if rule.weekday is not None else 1,
            rule.weekday if rule.weekday is not None else 0,
            rule.date_from or "",
            rule.time_from or "",
        )

    return sorted(rows, key=sort_key)


# ---------------------------------------------------------------------
# Popis
# ---------------------------------------------------------------------

def describe_rule(rule, with_note=False):
    """Čitateľný popis pravidla, napr. "Každú stredu 14:00–22:00 (od
    01.10.2026 do 31.12.2026)" alebo "05.10.2026 – 07.10.2026 celý deň"."""

    if rule.weekday is not None:
        text = WEEKDAY_EVERY[rule.weekday]
    elif rule.date_from == rule.date_to:
        text = _date_text(rule.date_from)
    else:
        text = f"{_date_text(rule.date_from)} – {_date_text(rule.date_to)}"

    if rule.time_from:
        text += f" {rule.time_from}–{rule.time_to}"
    else:
        text += " celý deň"

    if rule.weekday is not None:
        if rule.date_from and rule.date_to:
            text += f" (od {_date_text(rule.date_from)} do {_date_text(rule.date_to)})"
        elif rule.date_from:
            text += f" (od {_date_text(rule.date_from)})"
        elif rule.date_to:
            text += f" (do {_date_text(rule.date_to)})"

    if with_note and rule.note:
        text += f" - {rule.note}"

    return text


# ---------------------------------------------------------------------
# Konflikty so smenami
# ---------------------------------------------------------------------

def _shift_interval(shift_date, start_time, end_time):
    day = datetime.strptime(shift_date, "%Y-%m-%d")
    start_hour, start_minute = (int(x) for x in start_time.split(":")[:2])
    end_hour, end_minute = (int(x) for x in end_time.split(":")[:2])

    start = day.replace(hour=start_hour, minute=start_minute)
    end = day.replace(hour=end_hour, minute=end_minute)

    if end <= start:
        end += timedelta(days=1)

    return start, end


def _rule_applies_on(rule, day):
    if rule.weekday is not None:
        if day.weekday() != rule.weekday:
            return False

        if rule.date_from and day.isoformat() < rule.date_from:
            return False

        if rule.date_to and day.isoformat() > rule.date_to:
            return False

        return True

    return rule.date_from <= day.isoformat() <= rule.date_to


def rule_conflicts(rule, shift_date, start_time, end_time):
    """Prekrýva sa smena s pravidlom nedostupnosti?"""

    start, end = _shift_interval(shift_date, start_time, end_time)
    current = start.replace(hour=0, minute=0)

    while current < end:
        next_day = current + timedelta(days=1)

        if _rule_applies_on(rule, current.date()):
            if rule.time_from:
                hours, minutes = (int(x) for x in rule.time_from.split(":"))
                window_start = current.replace(hour=hours, minute=minutes)
                hours, minutes = (int(x) for x in rule.time_to.split(":"))
                window_end = current.replace(hour=hours, minute=minutes)
            else:
                window_start, window_end = current, next_day

            if max(start, window_start) < min(end, window_end):
                return True

        current = next_day

    return False


def rules_for_employees(employee_ids, today=None):
    """``{employee_id: [pravidlá]}`` (len tie, ktoré neskončili)."""

    today = today or date.today()
    result = {employee_id: [] for employee_id in employee_ids}

    if not result:
        return result

    rules = db.session.execute(
        select(AvailabilityRule).where(
            AvailabilityRule.employee_id.in_(list(result)),
            (AvailabilityRule.date_to.is_(None))
            | (AvailabilityRule.date_to >= today.isoformat()),
        )
    ).scalars()

    for rule in rules:
        result[rule.employee_id].append(rule)

    return result


def shift_conflicts(employee_id, shift_date, start_time, end_time, rules=None):
    """Popisy pravidiel, s ktorými smena koliduje (prázdny zoznam = bez
    konfliktu). ``rules`` je zoznam pravidiel zamestnanca (inak sa
    načíta)."""

    if rules is None:
        rules = rules_for_employees([employee_id]).get(employee_id, [])

    return [
        describe_rule(rule, with_note=True)
        for rule in rules
        if rule_conflicts(rule, shift_date, start_time, end_time)
    ]


def conflicts_for_shifts(shift_rows, from_date=None):
    """``{id smeny: [popisy]}`` pre riadky zo ``shift_service`` (0 = id,
    3 = dátum, 4 = začiatok, 5 = koniec, 7 = zamestnanec); len smeny od
    ``from_date`` (ISO text) a len tie, ktoré majú konflikt."""

    rows = [row for row in shift_rows if from_date is None or row[3] >= from_date]
    rules = rules_for_employees({row[7] for row in rows})

    result = {}

    for row in rows:
        conflicts = shift_conflicts(row[7], row[3], row[4], row[5], rules=rules[row[7]])

        if conflicts:
            result[row[0]] = conflicts

    return result


def warning_text(conflicts):
    """Jedna veta upozornenia pre manažéra."""

    return (
        "Pozor: zamestnanec uviedol, že v tomto čase nemôže pracovať - "
        + "; ".join(conflicts)
        + "."
    )
