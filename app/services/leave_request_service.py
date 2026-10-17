"""Žiadosti o voľno: zamestnanec požiada, manažér schváli/zamietne.

Zamestnanec môže požiadať o plánované voľno (dovolenka, náhradné
voľno). PN a OČR sa žiadosťou nevybavujú - zapisuje ich manažér.

Schválenie vytvorí bežnú neprítomnosť (``Absence``) - kalendár a
plánovanie smien ju potom rešpektujú rovnako ako ručne zadanú.
"""

from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app.extensions import db
from app.orm_models import (
    Absence,
    Branch,
    Employee,
    LeaveRequest,
    Shift,
    User,
)

LEAVE_REQUEST_TYPES = ["Dovolenka", "Náhradné voľno"]

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_PENDING: "Čaká na schválenie",
    STATUS_APPROVED: "Schválená",
    STATUS_REJECTED: "Zamietnutá",
    STATUS_CANCELLED: "Zrušená",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _overlapping_absence(employee_id, start_date, end_date):
    return db.session.execute(
        select(Absence.id).where(
            Absence.employee_id == employee_id,
            Absence.start_date <= end_date,
            Absence.end_date >= start_date,
        )
    ).first()


def _overlapping_pending_request(employee_id, start_date, end_date):
    return db.session.execute(
        select(LeaveRequest.id).where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == STATUS_PENDING,
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        )
    ).first()


def create_request(
    employee_id, absence_type, start_date, end_date, note=None, today=None
):
    """Vytvorí žiadosť (``pending``). ``start_date``/``end_date`` sú
    ISO reťazce. Vráti ``(id, None)`` alebo ``(None, chyba)``."""

    today = today or date.today()

    if absence_type not in LEAVE_REQUEST_TYPES:
        return None, "Vyber druh voľna."

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return None, "Zadaj platné dátumy."

    if end < start:
        return None, "Koniec voľna nemôže byť pred jeho začiatkom."

    if start < today:
        return None, "Voľno nemožno žiadať spätne - začiatok musí byť dnes alebo neskôr."

    if _overlapping_absence(employee_id, start_date, end_date):
        return None, "V tomto období už máš zapísanú neprítomnosť."

    if _overlapping_pending_request(employee_id, start_date, end_date):
        return None, "V tomto období už máš čakajúcu žiadosť."

    leave_request = LeaveRequest(
        employee_id=employee_id,
        absence_type=absence_type,
        start_date=start_date,
        end_date=end_date,
        note=(note or "").strip() or None,
        status=STATUS_PENDING,
        created_at=_now(),
    )

    db.session.add(leave_request)
    db.session.commit()

    return leave_request.id, None


def _request_query():
    decider = User.__table__.alias("decider")

    return (
        select(
            LeaveRequest.id,
            LeaveRequest.employee_id,
            Employee.first_name,
            Employee.last_name,
            Employee.branch_id,
            Branch.name,
            LeaveRequest.absence_type,
            LeaveRequest.start_date,
            LeaveRequest.end_date,
            LeaveRequest.note,
            LeaveRequest.status,
            LeaveRequest.created_at,
            LeaveRequest.decided_at,
            decider.c.username,
            LeaveRequest.decision_note,
        )
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .join(Branch, Employee.branch_id == Branch.id)
        .outerjoin(decider, LeaveRequest.decided_by_user_id == decider.c.id)
    )


def get_requests(branch_ids=None, status=None, employee_id=None):
    """Žiadosti ako riadky (id, employee_id, meno, priezvisko,
    branch_id, názov pobočky, druh, od, do, poznámka, stav, vytvorená,
    rozhodnutá, kto rozhodol, poznámka k rozhodnutiu).

    Čakajúce sú zoradené od najstaršej (vybavuje sa v poradí), ostatné
    od najnovšej.
    """

    query = _request_query()

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    if status:
        query = query.where(LeaveRequest.status == status)

    if employee_id is not None:
        query = query.where(LeaveRequest.employee_id == employee_id)

    if status == STATUS_PENDING:
        query = query.order_by(LeaveRequest.created_at, LeaveRequest.id)
    else:
        query = query.order_by(
            LeaveRequest.created_at.desc(), LeaveRequest.id.desc()
        )

    return db.session.execute(query).all()


def get_request(request_id):
    return db.session.execute(
        _request_query().where(LeaveRequest.id == request_id)
    ).first()


def pending_count(branch_ids=None):
    query = (
        select(func.count(LeaveRequest.id))
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .where(LeaveRequest.status == STATUS_PENDING)
    )

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    return db.session.execute(query).scalar() or 0


def conflicting_shift_count(employee_id, start_date, end_date):
    """Koľko smien má zamestnanec naplánovaných v danom období."""

    return db.session.execute(
        select(func.count(Shift.id)).where(
            Shift.employee_id == employee_id,
            Shift.shift_date.between(start_date, end_date),
        )
    ).scalar() or 0


def day_count(start_date, end_date):
    return (
        date.fromisoformat(end_date) - date.fromisoformat(start_date)
    ).days + 1


def approve_request(request_id, user_id, decision_note=None):
    """Schváli žiadosť a vytvorí z nej neprítomnosť. Vráti
    ``(True, None)`` alebo ``(False, chyba)``."""

    leave_request = db.session.get(LeaveRequest, request_id)

    if leave_request is None:
        return False, "Žiadosť neexistuje."

    if leave_request.status != STATUS_PENDING:
        return False, "Táto žiadosť už bola vybavená."

    if _overlapping_absence(
        leave_request.employee_id,
        leave_request.start_date,
        leave_request.end_date,
    ):
        return False, (
            "Zamestnanec má v tomto období už zapísanú neprítomnosť - "
            "uprav ju alebo žiadosť zamietni."
        )

    absence = Absence(
        employee_id=leave_request.employee_id,
        absence_type=leave_request.absence_type,
        start_date=leave_request.start_date,
        end_date=leave_request.end_date,
        note=leave_request.note,
        created_at=_now(),
    )
    db.session.add(absence)
    db.session.flush()

    leave_request.status = STATUS_APPROVED
    leave_request.absence_id = absence.id
    leave_request.decided_at = _now()
    leave_request.decided_by_user_id = user_id
    leave_request.decision_note = (decision_note or "").strip() or None

    db.session.commit()

    return True, None


def reject_request(request_id, user_id, decision_note):
    """Zamietne žiadosť (dôvod je povinný). Vráti
    ``(True, None)`` alebo ``(False, chyba)``."""

    decision_note = (decision_note or "").strip()

    if not decision_note:
        return False, "Pri zamietnutí uveď dôvod."

    leave_request = db.session.get(LeaveRequest, request_id)

    if leave_request is None:
        return False, "Žiadosť neexistuje."

    if leave_request.status != STATUS_PENDING:
        return False, "Táto žiadosť už bola vybavená."

    leave_request.status = STATUS_REJECTED
    leave_request.decided_at = _now()
    leave_request.decided_by_user_id = user_id
    leave_request.decision_note = decision_note

    db.session.commit()

    return True, None


def cancel_request(request_id, employee_id):
    """Zamestnanec zruší vlastnú ČAKAJÚCU žiadosť. Vráti
    ``(True, None)`` alebo ``(False, chyba)``."""

    leave_request = db.session.get(LeaveRequest, request_id)

    if leave_request is None or leave_request.employee_id != employee_id:
        return False, "Žiadosť neexistuje."

    if leave_request.status != STATUS_PENDING:
        return False, (
            "Vybavenú žiadosť už nemožno zrušiť - kontaktuj manažéra."
        )

    leave_request.status = STATUS_CANCELLED
    leave_request.decided_at = _now()

    db.session.commit()

    return True, None
