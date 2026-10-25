"""Audit log - záznamy o tom, kto, kedy a čo v appke zmenil.

Zachytávanie je AUTOMATICKÉ na úrovni databázovej relácie (udalosť
``before_flush``): každé vytvorenie, úprava a zmazanie sledovaných
záznamov (smeny, absencie, žiadosti o voľno, zamestnanci, oddelenia,
pobočky, účty, šablóny, priradenia k oddeleniam) sa zapíše s menom
prihláseného používateľa. Žiadna zmena sa tak nedá "zabudnúť" pri novej
route. Zmeny mimo webu (``manage.py``) sa zapíšu ako "systém".

Výnimky, ktoré ORM udalosť nevidí (hromadný SQL ``UPDATE``/``DELETE``),
a hromadné akcie, kde by záznam za každý riadok bol len šum (kopírovanie
týždňa, zverejnenie týždňa), zapisuje volajúci výslovne cez
``record_event`` (a riadkové záznamy potlačí cez ``suppressed``).

Zapísanie záznamu nikdy nesmie zhodiť samotnú operáciu - chyba pri
vytváraní záznamu sa len zaloguje.
"""

import contextlib
import contextvars
import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone

from flask import g, has_request_context
from flask_login import current_user
from sqlalchemy import delete, event, func, inspect, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.orm_models import (
    Absence,
    AuditLog,
    Branch,
    Department,
    Employee,
    EmployeeDepartment,
    LeaveRequest,
    Shift,
    ShiftSwap,
    ShiftTemplate,
    User,
)
from app.orm_models import AvailabilityRule

logger = logging.getLogger(__name__)

ENTITY_LABELS = {
    "shift": "Smeny",
    "week": "Týždne (hromadné akcie)",
    "absence": "Absencie",
    "leave_request": "Žiadosti o voľno",
    "swap": "Výmeny smien",
    "availability": "Dostupnosť",
    "employee": "Zamestnanci",
    "assignment": "Priradenia k oddeleniam",
    "department": "Oddelenia",
    "template": "Šablóny smien",
    "branch": "Pobočky",
    "user": "Účty",
    "system": "Systém",
}

# Záznamy, ktoré vidí len admin (týkajú sa celej firmy, nie pobočky).
ADMIN_ONLY_ENTITIES = {"user", "branch", "system"}

_suppressed = contextvars.ContextVar("audit_suppressed", default=False)

_PENDING_IDS_KEY = "audit_pending_entity_ids"


@contextlib.contextmanager
def suppressed():
    """Počas bloku sa riadkové záznamy nezapisujú (hromadná akcia zapíše
    jeden súhrnný záznam sama)."""

    token = _suppressed.set(True)

    try:
        yield
    finally:
        _suppressed.reset(token)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _actor():
    """(ID, meno) prihláseného používateľa, inak ``(None, None)``.

    Výsledok sa pamätá po dobu požiadavky (kto zmenu spustil, sa počas
    nej nemení) a zisťovanie nikdy nevyhodí chybu - audit nesmie zhodiť
    samotnú operáciu (napr. pri nepoužiteľnom objekte používateľa).
    """

    if not has_request_context():
        return None, None

    cached = getattr(g, "_audit_actor", None)

    if cached is not None:
        return cached

    actor = (None, None)

    try:
        user = current_user

        if user and user.is_authenticated:
            actor = (user.id, user.username)
    except Exception:
        logger.debug("Audit: používateľa sa nepodarilo zistiť", exc_info=True)
        return None, None

    g._audit_actor = actor

    return actor


# ---------------------------------------------------------------------
# Čitateľné popisy zmien
# ---------------------------------------------------------------------

def _employee_name(session, employee_id):
    employee = session.get(Employee, employee_id) if employee_id else None

    if employee is None:
        return f"zamestnanec #{employee_id}"

    return f"{employee.first_name} {employee.last_name}"


def _department_name(session, department_id):
    department = session.get(Department, department_id) if department_id else None

    return department.name if department else "—"


def _employee_branch_id(session, employee_id):
    employee = session.get(Employee, employee_id) if employee_id else None

    return employee.branch_id if employee else None


def _changes(obj, labels, formatters=None):
    """Zmenené stĺpce ako zoznam ``"štítok: pôvodná → nová"``."""

    formatters = formatters or {}
    state = inspect(obj)
    parts = []

    for key, label in labels.items():
        history = state.attrs[key].history

        if not history.has_changes():
            continue

        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None

        fmt = formatters.get(key, _plain)
        parts.append(f"{label}: {fmt(old)} → {fmt(new)}")

    return parts


def _plain(value):
    return "—" if value in (None, "") else str(value)


def _active_text(value):
    return "aktívny" if value else "neaktívny"


def _published_text(value):
    return "zverejnená" if value else "koncept"


def _shift_text(session, shift):
    text = (
        f"{_employee_name(session, shift.employee_id)} "
        f"{shift.shift_date} {shift.start_time}–{shift.end_time} "
        f"({shift.shift_type}"
    )

    if shift.department_id:
        text += f", {_department_name(session, shift.department_id)}"

    return text + ")"


def _describe_shift(session, obj, op):
    branch_id = _employee_branch_id(session, obj.employee_id)

    if op == "create":
        return branch_id, f"Pridaná smena: {_shift_text(session, obj)}"

    if op == "delete":
        return branch_id, f"Zmazaná smena: {_shift_text(session, obj)}"

    labels = {
        "employee_id": "zamestnanec",
        "shift_date": "dátum",
        "start_time": "začiatok",
        "end_time": "koniec",
        "shift_type": "typ",
        "department_id": "oddelenie",
        "published": "stav",
    }
    parts = _changes(
        obj,
        labels,
        {
            "employee_id": lambda v: _employee_name(session, v),
            "department_id": lambda v: _department_name(session, v),
            "published": _published_text,
        },
    )

    if not parts:
        return None

    return branch_id, f"Upravená smena {_shift_text(session, obj)}: " + "; ".join(parts)


def _absence_text(session, absence):
    return (
        f"{_employee_name(session, absence.employee_id)} - "
        f"{absence.absence_type} {absence.start_date}–{absence.end_date}"
    )


def _describe_absence(session, obj, op):
    branch_id = _employee_branch_id(session, obj.employee_id)

    if op == "create":
        return branch_id, f"Pridaná neprítomnosť: {_absence_text(session, obj)}"

    if op == "delete":
        return branch_id, f"Zmazaná neprítomnosť: {_absence_text(session, obj)}"

    parts = _changes(
        obj,
        {
            "absence_type": "druh",
            "start_date": "od",
            "end_date": "do",
            "note": "poznámka",
        },
    )

    if not parts:
        return None

    return branch_id, f"Upravená neprítomnosť {_absence_text(session, obj)}: " + "; ".join(parts)


def _describe_leave_request(session, obj, op):
    from app.services.leave_request_service import STATUS_LABELS

    branch_id = _employee_branch_id(session, obj.employee_id)
    text = (
        f"{_employee_name(session, obj.employee_id)} - "
        f"{obj.absence_type} {obj.start_date}–{obj.end_date}"
    )

    if op == "create":
        return branch_id, f"Podaná žiadosť o voľno: {text}"

    if op == "delete":
        return branch_id, f"Zmazaná žiadosť o voľno: {text}"

    state = inspect(obj)

    if not state.attrs.status.history.has_changes():
        return None

    summary = f"Žiadosť o voľno ({text}): {STATUS_LABELS.get(obj.status, obj.status).lower()}"

    if obj.decision_note:
        summary += f" - {obj.decision_note}"

    return branch_id, summary


def _describe_availability(session, obj, op):
    from app.services.availability_service import describe_rule

    branch_id = _employee_branch_id(session, obj.employee_id)
    text = (
        f"{_employee_name(session, obj.employee_id)} - "
        f"{describe_rule(obj, with_note=True)}"
    )

    if op == "create":
        return branch_id, f"Zapísaná nedostupnosť: {text}"

    if op == "delete":
        return branch_id, f"Zrušená nedostupnosť: {text}"

    return None


def _describe_swap(session, obj, op):
    from app.services.shift_swap_service import STATUS_LABELS

    branch_id = _employee_branch_id(session, obj.proposer_employee_id)
    text = (
        f"{_employee_name(session, obj.proposer_employee_id)} → "
        f"{_employee_name(session, obj.colleague_employee_id)}: "
        f"{obj.offered_text}"
    )

    if obj.counter_text:
        text += f" ⇄ {obj.counter_text}"

    if op == "create":
        return branch_id, f"Navrhnutá výmena smien ({text})"

    if op == "delete":
        return branch_id, f"Zmazaná žiadosť o výmenu smien ({text})"

    if not inspect(obj).attrs.status.history.has_changes():
        return None

    summary = (
        f"Výmena smien ({text}): "
        f"{STATUS_LABELS.get(obj.status, obj.status).lower()}"
    )

    if obj.decision_note:
        summary += f" - {obj.decision_note}"

    return branch_id, summary


def _describe_employee(session, obj, op):
    text = f"{obj.first_name} {obj.last_name} ({obj.position})"

    if op == "create":
        return obj.branch_id, f"Pridaný zamestnanec: {text}"

    if op == "delete":
        return obj.branch_id, f"Zmazaný zamestnanec: {text}"

    parts = _changes(
        obj,
        {
            "first_name": "meno",
            "last_name": "priezvisko",
            "position": "pozícia",
            "weekly_hours": "týždenný fond",
            "active": "stav",
        },
        {"active": _active_text},
    )

    if not parts:
        return None

    return obj.branch_id, f"Upravený zamestnanec {text}: " + "; ".join(parts)


def _describe_department(session, obj, op):
    if op == "create":
        return obj.branch_id, f"Pridané oddelenie: {obj.name}"

    if op == "delete":
        return obj.branch_id, f"Zmazané oddelenie: {obj.name}"

    parts = _changes(
        obj,
        {"name": "názov", "min_staff": "min. počet ľudí", "active": "stav"},
        {"active": _active_text},
    )

    if not parts:
        return None

    return obj.branch_id, f"Upravené oddelenie {obj.name}: " + "; ".join(parts)


def _describe_branch(session, obj, op):
    if op == "create":
        return None, f"Pridaná pobočka: {obj.name}"

    if op == "delete":
        return None, f"Zmazaná pobočka: {obj.name}"

    parts = _changes(
        obj,
        {"name": "názov", "active": "stav"},
        {"active": lambda v: "aktívna" if v else "neaktívna"},
    )

    if not parts:
        return None

    return None, f"Upravená pobočka {obj.name}: " + "; ".join(parts)


def _describe_user(session, obj, op):
    if op == "create":
        return None, f"Vytvorený účet: {obj.username} ({obj.role})"

    if op == "delete":
        return None, f"Zmazaný účet: {obj.username} ({obj.role})"

    state = inspect(obj)
    parts = _changes(
        obj,
        {
            "username": "meno",
            "role": "rola",
            "email": "e-mail",
            "notify_email": "upozornenia",
        },
        {"notify_email": lambda v: "zapnuté" if v else "vypnuté"},
    )

    if state.attrs.password_hash.history.has_changes():
        parts.append("heslo zmenené")

    branches = state.attrs.branches.history

    if branches.added or branches.deleted:
        removed = ", ".join(sorted(b.name for b in branches.deleted)) or "—"
        added = ", ".join(sorted(b.name for b in branches.added)) or "—"
        parts.append(f"pobočky: odobraté {removed}; pridané {added}")

    if not parts:
        return None

    return None, f"Upravený účet {obj.username}: " + "; ".join(parts)


def _describe_template(session, obj, op):
    if op == "create":
        return obj.branch_id, f"Uložená šablóna smien: {obj.name}"

    if op == "delete":
        return obj.branch_id, f"Zmazaná šablóna smien: {obj.name}"

    parts = _changes(obj, {"name": "názov"})

    if not parts:
        return None

    return obj.branch_id, f"Upravená šablóna {obj.name}: " + "; ".join(parts)


def _describe_assignment(session, obj, op):
    branch_id = _employee_branch_id(session, obj.employee_id)
    text = (
        f"{_employee_name(session, obj.employee_id)} → "
        f"{_department_name(session, obj.department_id)} "
        f"({obj.weekly_hours} h/týždeň)"
    )

    if op == "create":
        return branch_id, f"Priradenie k oddeleniu: {text}"

    if op == "delete":
        return branch_id, f"Zrušené priradenie k oddeleniu: {text}"

    parts = _changes(
        obj,
        {"department_id": "oddelenie", "weekly_hours": "hodiny"},
        {"department_id": lambda v: _department_name(session, v)},
    )

    if not parts:
        return None

    return branch_id, f"Upravené priradenie {text}: " + "; ".join(parts)


# model -> (typ entity, popisovač)
_TRACKED = {
    Shift: ("shift", _describe_shift),
    Absence: ("absence", _describe_absence),
    LeaveRequest: ("leave_request", _describe_leave_request),
    ShiftSwap: ("swap", _describe_swap),
    AvailabilityRule: ("availability", _describe_availability),
    Employee: ("employee", _describe_employee),
    Department: ("department", _describe_department),
    Branch: ("branch", _describe_branch),
    User: ("user", _describe_user),
    ShiftTemplate: ("template", _describe_template),
    EmployeeDepartment: ("assignment", _describe_assignment),
}


# ---------------------------------------------------------------------
# Zachytávanie zmien
# ---------------------------------------------------------------------

def _before_flush(session, flush_context, instances):
    if _suppressed.get():
        return

    actor_id, actor_name = _actor()
    created = []

    with session.no_autoflush:
        for objects, op in (
            (list(session.new), "create"),
            (list(session.dirty), "update"),
            (list(session.deleted), "delete"),
        ):
            for obj in objects:
                tracked = _TRACKED.get(type(obj))

                if tracked is None:
                    continue

                entity_type, describe = tracked

                try:
                    described = describe(session, obj, op)
                except Exception:
                    # Audit nesmie zhodiť samotnú operáciu.
                    logger.exception(
                        "Audit: nepodarilo sa popísať zmenu (%s, %s)",
                        entity_type,
                        op,
                    )
                    continue

                if described is None:
                    continue

                branch_id, summary = described

                row = AuditLog(
                    created_at=_now(),
                    user_id=actor_id,
                    username=actor_name,
                    action=f"{entity_type}.{op}",
                    entity_type=entity_type,
                    entity_id=None if op == "create" else getattr(obj, "id", None),
                    branch_id=branch_id,
                    summary=summary,
                )
                session.add(row)

                if op == "create":
                    created.append((row, obj))

    if created:
        session.info.setdefault(_PENDING_IDS_KEY, []).extend(created)


def _after_flush_postexec(session, flush_context):
    # ID nových záznamov vznikne až pri zápise do databázy. Zmeny sa
    # robia v "postexec" udalosti: zmena urobená v ``after_flush`` by sa
    # nezapísala (SQLAlchemy ju po flush-i vyhlási za už uloženú).
    for row, obj in session.info.pop(_PENDING_IDS_KEY, []):
        row.entity_id = getattr(obj, "id", None)


def init_audit():
    """Zaregistruje zachytávanie zmien (bezpečné volať opakovane)."""

    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)

    if not event.contains(Session, "after_flush_postexec", _after_flush_postexec):
        event.listen(Session, "after_flush_postexec", _after_flush_postexec)


def record_event(action, summary, entity_type, entity_id=None, branch_id=None):
    """Výslovný záznam (hromadné akcie, zmeny mimo ORM). Zapisuje sa
    samostatne po dokončení samotnej operácie a nikdy nevyhodí chybu."""

    actor_id, actor_name = _actor()

    try:
        db.session.add(
            AuditLog(
                created_at=_now(),
                user_id=actor_id,
                username=actor_name,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                branch_id=branch_id,
                summary=summary,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Audit: záznam sa nepodarilo uložiť (%s)", action)


# ---------------------------------------------------------------------
# Čas (zobrazenie v lokálnom čase)
# ---------------------------------------------------------------------

def _timezone():
    name = os.environ.get("APP_TIMEZONE", "Europe/Bratislava")

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def to_local(created_at):
    """UTC reťazec ``YYYY-MM-DD HH:MM:SS`` -> lokálny čas
    ``dd.mm.yyyy HH:MM:SS`` (časová zóna z ``APP_TIMEZONE``, predvolene
    Europe/Bratislava)."""

    try:
        parsed = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return created_at or ""

    local = parsed.replace(tzinfo=timezone.utc).astimezone(_timezone())

    return local.strftime("%d.%m.%Y %H:%M:%S")


def _local_day_start_utc(day):
    """Začiatok lokálneho dňa ``day`` (``date``) ako UTC reťazec."""

    local_midnight = datetime(day.year, day.month, day.day, tzinfo=_timezone())

    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------
# Čítanie
# ---------------------------------------------------------------------

def _filtered_query(
    branch_ids=None, entity_type=None, username=None, date_from=None, date_to=None
):
    query = select(AuditLog)

    if branch_ids is not None:
        query = query.where(
            AuditLog.branch_id.in_(list(branch_ids)),
            AuditLog.entity_type.notin_(ADMIN_ONLY_ENTITIES),
        )

    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)

    if username:
        query = query.where(AuditLog.username == username)

    if date_from is not None:
        query = query.where(AuditLog.created_at >= _local_day_start_utc(date_from))

    if date_to is not None:
        query = query.where(
            AuditLog.created_at < _local_day_start_utc(date_to + timedelta(days=1))
        )

    return query


def get_audit_entries(
    branch_ids=None,
    entity_type=None,
    username=None,
    date_from=None,
    date_to=None,
    limit=50,
    offset=0,
):
    """Vráti ``(záznamy, celkový počet)`` od najnovšieho. ``branch_ids``
    obmedzí výber na pobočky manažéra (záznamy o účtoch a pobočkách sú
    len pre admina); ``None`` = všetko."""

    query = _filtered_query(
        branch_ids, entity_type, username, date_from, date_to
    )

    total = db.session.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar()

    rows = (
        db.session.execute(
            query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )

    return rows, total


def get_audit_usernames(branch_ids=None):
    """Mená používateľov, ktorí sa v záznamoch vyskytujú (pre filter)."""

    query = select(AuditLog.username).where(AuditLog.username.isnot(None))

    if branch_ids is not None:
        query = query.where(
            AuditLog.branch_id.in_(list(branch_ids)),
            AuditLog.entity_type.notin_(ADMIN_ONLY_ENTITIES),
        )

    return sorted(set(db.session.execute(query).scalars()), key=str.lower)


def entries_to_csv(entries):
    """CSV (UTF-8 s BOM - Excel ho tak otvorí správne) zo záznamov."""

    buffer = io.StringIO()
    buffer.write("\ufeff")

    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Čas", "Používateľ", "Typ", "Akcia", "Popis"])

    for entry in entries:
        writer.writerow(
            [
                to_local(entry.created_at),
                entry.username or "systém",
                ENTITY_LABELS.get(entry.entity_type, entry.entity_type),
                entry.action,
                entry.summary,
            ]
        )

    return buffer.getvalue()


# ---------------------------------------------------------------------
# Údržba
# ---------------------------------------------------------------------

def prune_older_than(days):
    """Zmaže záznamy staršie ako ``days`` dní. Vráti ich počet."""

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    result = db.session.execute(
        delete(AuditLog).where(AuditLog.created_at < cutoff)
    )
    db.session.commit()

    return result.rowcount
