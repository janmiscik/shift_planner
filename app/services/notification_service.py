"""E-mailové upozornenia na udalosti v appke.

Kto dostane e-mail (ak má v účte adresu a nevypol upozornenia):
  - zamestnanec: zverejnený rozpis jeho smien, rozhodnutie o jeho
    žiadosti o voľno, návrh výmeny smeny od kolegu, výsledok výmeny,
  - manažér pobočky: nová žiadosť o voľno, výmena smien čakajúca na
    schválenie (ak pobočka nemá manažéra s adresou, dostanú ich admini),
  - navrhovateľ výmeny: odmietnutie kolegom a rozhodnutie manažéra.
Človeka, ktorého sa udalosť týka ako zainteresovanej strany, nenotifikujeme
ako schvaľovateľa (nevybavuje vlastné žiadosti).

Funkcie e-mail len uložia do fronty (``email_service.enqueue_email``) a
NIKDY nevyhodia chybu - upozornenie nesmie zhodiť samotnú operáciu.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.extensions import db
from app.orm_models import (
    Branch,
    Department,
    Employee,
    LeaveRequest,
    Shift,
    ShiftSwap,
    User,
)
from app.services.email_service import base_url, enqueue_email, smtp_configured

logger = logging.getLogger(__name__)

_WEEKDAYS = ["Po", "Ut", "St", "Št", "Pi", "So", "Ne"]


def _date_text(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _link(path):
    base = base_url()

    return f"{base}{path}" if base else None


def _eligible(user):
    return user is not None and bool(user.email) and bool(user.notify_email)


def _user_for_employee(employee_id):
    return db.session.execute(
        select(User).where(User.employee_id == employee_id)
    ).scalar_one_or_none()


def _managers_for_branch(branch_id, exclude_employee_ids=()):
    """Manažéri pobočky, ktorí môžu dostať e-mail; ak nikto, tak admini."""

    def usable(user):
        return _eligible(user) and user.employee_id not in exclude_employee_ids

    managers = [
        user
        for user in db.session.execute(
            select(User).where(User.role == "manager")
        ).scalars()
        if any(branch.id == branch_id for branch in user.branches)
        and usable(user)
    ]

    if managers:
        return managers

    return [
        user
        for user in db.session.execute(
            select(User).where(User.role == "admin")
        ).scalars()
        if usable(user)
    ]


def _greeting(user):
    name = (
        user.employee.first_name
        if user.employee is not None
        else user.username
    )

    return f"Dobrý deň, {name},"


def _footer(link_path=None, link_label="Otvoriť v aplikácii"):
    lines = []

    link = _link(link_path) if link_path else None

    if link:
        lines.append(f"{link_label}: {link}")

    settings = _link("/account/settings")

    if settings:
        lines.append(f"Upozornenia vypneš v Môj účet: {settings}")
    else:
        lines.append("Upozornenia vypneš v aplikácii v sekcii Môj účet.")

    return "\n".join(lines)


def _send_to(users, subject, body_lines, link_path=None, link_label="Otvoriť v aplikácii"):
    """Pošle (uloží do fronty) rovnakú správu každému z ``users``."""

    seen = set()

    for user in users:
        if not _eligible(user) or user.email in seen:
            continue

        seen.add(user.email)

        body = "\n".join(
            [
                _greeting(user),
                "",
                *body_lines,
                "",
                _footer(link_path, link_label),
                "",
                "-- Shift Planner",
            ]
        )

        enqueue_email(user.email, subject, body)


def _safe(function):
    """Upozornenie nikdy nezhodí volajúceho."""

    def wrapper(*args, **kwargs):
        if not smtp_configured():
            return

        try:
            function(*args, **kwargs)
        except Exception:
            db.session.rollback()
            logger.exception("Upozornenie sa nepodarilo pripraviť (%s).", function.__name__)

    wrapper.__name__ = function.__name__

    return wrapper


def _employee_name(employee):
    return f"{employee.first_name} {employee.last_name}"


# ---------------------------------------------------------------------
# Zverejnený rozpis
# ---------------------------------------------------------------------

@_safe
def notify_schedule_published(shift_ids, monday, branch_id):
    """Každý zamestnanec, ktorému sa zverejnila aspoň jedna smena,
    dostane svoj zverejnený rozpis na daný týždeň."""

    if not shift_ids:
        return

    sunday = monday + timedelta(days=6)
    branch = db.session.get(Branch, branch_id)
    week = f"{monday.strftime('%d.%m.')}–{sunday.strftime('%d.%m.%Y')}"

    employee_ids = sorted(
        set(
            db.session.execute(
                select(Shift.employee_id).where(Shift.id.in_(list(shift_ids)))
            ).scalars()
        )
    )

    for employee_id in employee_ids:
        user = _user_for_employee(employee_id)

        if not _eligible(user):
            continue

        rows = db.session.execute(
            select(
                Shift.shift_date,
                Shift.start_time,
                Shift.end_time,
                Shift.shift_type,
                Department.name,
            )
            .outerjoin(Department, Shift.department_id == Department.id)
            .where(
                Shift.employee_id == employee_id,
                Shift.published == 1,
                Shift.shift_date.between(monday.isoformat(), sunday.isoformat()),
            )
            .order_by(Shift.shift_date, Shift.start_time)
        ).all()

        lines = [f"Zverejnili sme rozpis smien na týždeň {week}. Tvoje smeny:", ""]

        for shift_date, start, end, shift_type, department in rows:
            weekday = _WEEKDAYS[date.fromisoformat(shift_date).weekday()]
            extra = f", {department}" if department else ""
            lines.append(
                f"- {weekday} {_date_text(shift_date)}  {start}–{end} "
                f"({shift_type}{extra})"
            )

        _send_to(
            [user],
            f"Nový rozpis smien: {week}"
            + (f" ({branch.name})" if branch else ""),
            lines,
            "/my-schedule",
            "Celý rozpis",
        )


# ---------------------------------------------------------------------
# Žiadosti o voľno
# ---------------------------------------------------------------------

def _leave_text(request):
    period = _date_text(request.start_date)

    if request.end_date != request.start_date:
        period += f" – {_date_text(request.end_date)}"

    return f"{request.absence_type.lower()} {period}"


@_safe
def notify_leave_requested(request_id):
    """Nová žiadosť o voľno -> manažéri pobočky."""

    request = db.session.get(LeaveRequest, request_id)
    employee = db.session.get(Employee, request.employee_id)

    lines = [
        f"{_employee_name(employee)} žiada o voľno: {_leave_text(request)}.",
    ]

    if request.note:
        lines.append(f"Poznámka: {request.note}")

    _send_to(
        _managers_for_branch(employee.branch_id, (employee.id,)),
        f"Nová žiadosť o voľno: {_employee_name(employee)}",
        lines,
        "/leave-requests",
        "Vybaviť žiadosť",
    )


@_safe
def notify_leave_decided(request_id):
    """Rozhodnutie o žiadosti -> zamestnanec."""

    request = db.session.get(LeaveRequest, request_id)

    if request.status not in ("approved", "rejected"):
        return

    approved = request.status == "approved"
    lines = [
        f"Tvoja žiadosť o voľno ({_leave_text(request)}) bola "
        + ("schválená." if approved else "zamietnutá.")
    ]

    if request.decision_note:
        lines.append(f"Poznámka manažéra: {request.decision_note}")

    _send_to(
        [_user_for_employee(request.employee_id)],
        "Žiadosť o voľno " + ("schválená" if approved else "zamietnutá"),
        lines,
        "/my-requests",
        "Moje žiadosti",
    )


# ---------------------------------------------------------------------
# Výmena smien
# ---------------------------------------------------------------------

def _swap_people(swap):
    return (
        db.session.get(Employee, swap.proposer_employee_id),
        db.session.get(Employee, swap.colleague_employee_id),
    )


@_safe
def notify_swap_proposed(swap_id):
    """Nový návrh výmeny -> kolega."""

    swap = db.session.get(ShiftSwap, swap_id)
    proposer, colleague = _swap_people(swap)

    lines = [
        f"{_employee_name(proposer)} ti ponúka smenu {swap.offered_text}.",
        (
            "Chce za ňu jednu z tvojich smien."
            if swap.wants_swap
            else "Na oplátku nič nežiada."
        ),
    ]

    if swap.note:
        lines.append(f"Poznámka: {swap.note}")

    _send_to(
        [_user_for_employee(colleague.id)],
        f"{_employee_name(proposer)} ti navrhuje výmenu smeny",
        lines,
        "/my-swaps",
        "Odpovedať",
    )


@_safe
def notify_swap_ready_for_manager(swap_id):
    """Kolega prijal -> manažéri pobočky (schválenie)."""

    swap = db.session.get(ShiftSwap, swap_id)
    proposer, colleague = _swap_people(swap)

    text = f"{_employee_name(proposer)} → {_employee_name(colleague)}: {swap.offered_text}"

    if swap.counter_text:
        text += f" ⇄ {swap.counter_text}"

    _send_to(
        _managers_for_branch(proposer.branch_id, (proposer.id, colleague.id)),
        "Výmena smien čaká na schválenie",
        [f"Kolega súhlasil s výmenou smien: {text}.", "Treba ju schváliť alebo zamietnuť."],
        "/swap-requests",
        "Vybaviť výmenu",
    )


@_safe
def notify_swap_decided(swap_id):
    """Výsledok výmeny -> zainteresovaní (schválená/zamietnutá obom,
    odmietnutie kolegom len navrhovateľovi)."""

    swap = db.session.get(ShiftSwap, swap_id)
    proposer, colleague = _swap_people(swap)

    text = f"{swap.offered_text}"

    if swap.counter_text:
        text += f" ⇄ {swap.counter_text}"

    if swap.status == "approved":
        subject = "Výmena smien schválená"
        lines = [f"Výmena smien bola schválená: {text}.", "Tvoj rozpis sa zmenil."]
        recipients = [proposer, colleague]
    elif swap.status == "rejected":
        subject = "Výmena smien zamietnutá"
        lines = [f"Manažér zamietol výmenu smien: {text}. Smeny ostávajú, ako boli."]

        if swap.decision_note:
            lines.append(f"Dôvod: {swap.decision_note}")

        recipients = [proposer, colleague]
    elif swap.status == "declined":
        subject = "Kolega odmietol tvoj návrh výmeny"
        lines = [f"{_employee_name(colleague)} odmietol výmenu smeny {text}."]
        recipients = [proposer]
    else:
        return

    _send_to(
        [_user_for_employee(employee.id) for employee in recipients],
        subject,
        lines,
        "/my-swaps",
        "Moje výmeny",
    )
