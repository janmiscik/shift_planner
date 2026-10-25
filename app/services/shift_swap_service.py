"""Výmena smien medzi kolegami.

Zamestnanec navrhne kolegovi z tej istej pobočky, aby prevzal jeho
smenu - buď len tak (odovzdanie), alebo výmenou za jednu zo smien
kolegu (``wants_swap``). Priebeh:

  1. navrhovateľ vytvorí žiadosť (``pending_colleague``),
  2. kolega ju prijme (pri výmene vyberie, ktorú svoju smenu dá na
     oplátku) alebo odmietne,
  3. manažér (alebo admin) ju schváli alebo zamietne. Pri schválení sa
     smeny skutočne prehodia, a to len ak NOVÉ pridelenie spĺňa všetky
     pravidlá ako pri ručnej úprave (prekrytie, odpočinok, týždenné
     fondy, absencia, oddelenie). Inak sa nič nezmení.

Vymieňať sa dajú len ZVEREJNENÉ smeny (zamestnanec nevidí koncepty) a
len budúce. Smena môže byť naraz v jednej otvorenej žiadosti.
"""

from datetime import date, datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.orm_models import (
    Branch,
    Employee,
    Shift,
    ShiftSwap,
    User,
)
from app.services.availability_service import shift_conflicts
from app.services.shift_service import validate_shift

STATUS_PENDING_COLLEAGUE = "pending_colleague"
STATUS_PENDING_MANAGER = "pending_manager"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DECLINED = "declined"
STATUS_CANCELLED = "cancelled"

OPEN_STATUSES = (STATUS_PENDING_COLLEAGUE, STATUS_PENDING_MANAGER)

STATUS_LABELS = {
    STATUS_PENDING_COLLEAGUE: "Čaká na kolegu",
    STATUS_PENDING_MANAGER: "Čaká na schválenie manažérom",
    STATUS_APPROVED: "Schválená",
    STATUS_REJECTED: "Zamietnutá manažérom",
    STATUS_DECLINED: "Odmietnutá kolegom",
    STATUS_CANCELLED: "Zrušená",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def shift_text(shift):
    return (
        f"{shift.shift_date} {shift.start_time}–{shift.end_time} "
        f"({shift.shift_type})"
    )


def _employee_name(employee):
    return f"{employee.first_name} {employee.last_name}"


def _open_shift_ids(exclude_swap_id=None):
    """ID smien, ktoré sú práve v nejakej otvorenej žiadosti."""

    query = select(ShiftSwap.offered_shift_id, ShiftSwap.counter_shift_id).where(
        ShiftSwap.status.in_(OPEN_STATUSES)
    )

    if exclude_swap_id is not None:
        query = query.where(ShiftSwap.id != exclude_swap_id)

    ids = set()

    for offered, counter in db.session.execute(query).all():
        ids.update(value for value in (offered, counter) if value)

    return ids


def _swappable(shift, owner_id, today, exclude_swap_id=None):
    """Chybová správa, ak smenu nemožno vymeniť, inak ``None``."""

    if shift is None or shift.employee_id != owner_id:
        return "Smena už neexistuje alebo nepatrí tomuto zamestnancovi."

    if not shift.published:
        return "Vymeniť sa dá len zverejnená smena."

    if shift.shift_date < today.isoformat():
        return "Smena už prebehla - minulé smeny sa vymeniť nedajú."

    if shift.id in _open_shift_ids(exclude_swap_id):
        return "Táto smena je už v inej otvorenej žiadosti o výmenu."

    return None


def swappable_shifts(employee_id, today=None):
    """Budúce zverejnené smeny zamestnanca, ktoré nie sú v otvorenej
    žiadosti (smeny, ktoré môže ponúknuť/dať na oplátku)."""

    today = today or date.today()
    open_ids = _open_shift_ids()

    shifts = db.session.execute(
        select(Shift)
        .where(
            Shift.employee_id == employee_id,
            Shift.published == 1,
            Shift.shift_date >= today.isoformat(),
        )
        .order_by(Shift.shift_date, Shift.start_time)
    ).scalars()

    return [shift for shift in shifts if shift.id not in open_ids]


def candidate_colleagues(employee_id):
    """Kolegovia, ktorým možno výmenu navrhnúť: tá istá pobočka, aktívni
    a s prihlasovacím účtom (inak by žiadosť nemal kto prijať).
    Vráti ``[(id, meno), ...]``."""

    employee = db.session.get(Employee, employee_id)

    if employee is None:
        return []

    rows = db.session.execute(
        select(Employee.id, Employee.first_name, Employee.last_name)
        .join(User, User.employee_id == Employee.id)
        .where(
            Employee.branch_id == employee.branch_id,
            Employee.active == 1,
            Employee.id != employee_id,
        )
        .order_by(Employee.last_name, Employee.first_name)
    ).all()

    return [(row[0], f"{row[1]} {row[2]}") for row in rows]


def create_swap(
    proposer_id, colleague_id, offered_shift_id, wants_swap=False, note=None,
    today=None,
):
    """Vytvorí žiadosť o výmenu. Vráti ``(id, None)`` alebo
    ``(None, chyba)``."""

    today = today or date.today()

    proposer = db.session.get(Employee, proposer_id)

    if proposer is None or not proposer.active:
        return None, "Zamestnanec neexistuje alebo je neaktívny."

    if colleague_id not in {c[0] for c in candidate_colleagues(proposer_id)}:
        return None, "Vyber kolegu z tvojej pobočky, ktorý má prihlasovací účet."

    shift = db.session.get(Shift, offered_shift_id) if offered_shift_id else None
    error = _swappable(shift, proposer_id, today)

    if error:
        return None, error

    swap = ShiftSwap(
        proposer_employee_id=proposer_id,
        colleague_employee_id=colleague_id,
        offered_shift_id=shift.id,
        wants_swap=1 if wants_swap else 0,
        offered_text=shift_text(shift),
        note=(note or "").strip() or None,
        status=STATUS_PENDING_COLLEAGUE,
        created_at=_now(),
    )

    db.session.add(swap)
    db.session.commit()

    from app.services.notification_service import notify_swap_proposed

    notify_swap_proposed(swap.id)

    return swap.id, None


def colleague_respond(
    swap_id, colleague_employee_id, accept, counter_shift_id=None, today=None
):
    """Kolega žiadosť prijme alebo odmietne. Vráti ``(True, None)`` alebo
    ``(False, chyba)``."""

    today = today or date.today()

    swap = db.session.get(ShiftSwap, swap_id)

    if swap is None or swap.colleague_employee_id != colleague_employee_id:
        return False, "Žiadosť neexistuje."

    if swap.status != STATUS_PENDING_COLLEAGUE:
        return False, "Na túto žiadosť sa už odpovedalo alebo bola zrušená."

    if not accept:
        swap.status = STATUS_DECLINED
        swap.colleague_responded_at = _now()
        db.session.commit()

        from app.services.notification_service import notify_swap_decided

        notify_swap_decided(swap.id)

        return True, None

    offered = db.session.get(Shift, swap.offered_shift_id) if swap.offered_shift_id else None
    error = _swappable(offered, swap.proposer_employee_id, today, exclude_swap_id=swap.id)

    if error:
        swap.status = STATUS_CANCELLED
        swap.colleague_responded_at = _now()
        db.session.commit()

        return False, f"Žiadosť sa zrušila: {error}"

    if swap.wants_swap:
        counter = db.session.get(Shift, counter_shift_id) if counter_shift_id else None

        if counter is None:
            return False, "Vyber svoju smenu, ktorú dáš na oplátku."

        error = _swappable(counter, colleague_employee_id, today, exclude_swap_id=swap.id)

        if error:
            return False, error

        swap.counter_shift_id = counter.id
        swap.counter_text = shift_text(counter)

    swap.status = STATUS_PENDING_MANAGER
    swap.colleague_responded_at = _now()
    db.session.commit()

    from app.services.notification_service import (
        notify_swap_ready_for_manager,
    )

    notify_swap_ready_for_manager(swap.id)

    return True, None


def cancel_swap(swap_id, employee_id):
    """Zruší otvorenú žiadosť - môže ju navrhovateľ aj kolega. Vráti
    ``(True, None)`` alebo ``(False, chyba)``."""

    swap = db.session.get(ShiftSwap, swap_id)

    if swap is None or employee_id not in (
        swap.proposer_employee_id,
        swap.colleague_employee_id,
    ):
        return False, "Žiadosť neexistuje."

    if swap.status not in OPEN_STATUSES:
        return False, "Vybavenú žiadosť už nemožno zrušiť."

    swap.status = STATUS_CANCELLED
    swap.decided_at = _now()

    db.session.commit()

    return True, None


def reject_swap(swap_id, user_id, decision_note):
    """Manažér žiadosť zamietne (dôvod je povinný)."""

    decision_note = (decision_note or "").strip()

    if not decision_note:
        return False, "Pri zamietnutí uveď dôvod."

    swap = db.session.get(ShiftSwap, swap_id)

    if swap is None:
        return False, "Žiadosť neexistuje."

    if swap.status != STATUS_PENDING_MANAGER:
        return False, "Túto žiadosť teraz nemožno zamietnuť (nečaká na manažéra)."

    swap.status = STATUS_REJECTED
    swap.decided_at = _now()
    swap.decided_by_user_id = user_id
    swap.decision_note = decision_note

    db.session.commit()

    from app.services.notification_service import notify_swap_decided

    notify_swap_decided(swap.id)

    return True, None


def approve_swap(swap_id, user_id, decision_note=None, today=None):
    """Manažér žiadosť schváli a smeny prehodí.

    Vráti ``(True, [upozornenia])`` (upozornenia na nedostupnosť
    zamestnanca - neblokujú) alebo ``(False, [chyby])``. Pri akejkoľvek
    chybe sa nič nezmení (transakcia sa vráti späť).
    """

    today = today or date.today()

    swap = db.session.get(ShiftSwap, swap_id)

    if swap is None:
        return False, ["Žiadosť neexistuje."]

    if swap.status != STATUS_PENDING_MANAGER:
        return False, ["Túto žiadosť teraz nemožno schváliť (nečaká na manažéra)."]

    proposer = db.session.get(Employee, swap.proposer_employee_id)
    colleague = db.session.get(Employee, swap.colleague_employee_id)

    offered = db.session.get(Shift, swap.offered_shift_id) if swap.offered_shift_id else None
    counter = (
        db.session.get(Shift, swap.counter_shift_id)
        if swap.wants_swap and swap.counter_shift_id
        else None
    )

    stale = []

    for employee in (proposer, colleague):
        if employee is None or not employee.active:
            stale.append("Niektorý zo zamestnancov už nie je aktívny.")
            break

    error = _swappable(offered, swap.proposer_employee_id, today, exclude_swap_id=swap.id)

    if error:
        stale.append(f"Ponúkaná smena: {error}")

    if swap.wants_swap:
        error = _swappable(counter, swap.colleague_employee_id, today, exclude_swap_id=swap.id)

        if error:
            stale.append(f"Smena na oplátku: {error}")

    if stale:
        return False, [*stale, "Žiadosť zamietni alebo požiadaj o novú."]

    try:
        offered.employee_id = colleague.id

        if counter is not None:
            counter.employee_id = proposer.id

        # Po prehodení musí NOVÉ pridelenie spĺňať všetky pravidlá.
        db.session.flush()

        errors = []

        for shift, owner in ((offered, colleague), (counter, proposer)):
            if shift is None:
                continue

            for message in validate_shift(
                owner.id,
                shift.shift_date,
                shift.start_time,
                shift.end_time,
                department_id=shift.department_id,
                exclude_shift_id=shift.id,
            ):
                errors.append(
                    f"{_employee_name(owner)} ({shift_text(shift)}): {message}"
                )

        if errors:
            db.session.rollback()

            return False, errors

        swap.status = STATUS_APPROVED
        swap.decided_at = _now()
        swap.decided_by_user_id = user_id
        swap.decision_note = (decision_note or "").strip() or None

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    from app.services.notification_service import notify_swap_decided

    notify_swap_decided(swap.id)

    # Nedostupnosť je mäkké obmedzenie: výmenu neblokuje, len upozorní.
    warnings = []

    for shift, owner in ((offered, colleague), (counter, proposer)):
        if shift is None:
            continue

        conflicts = shift_conflicts(
            owner.id, shift.shift_date, shift.start_time, shift.end_time
        )

        if conflicts:
            warnings.append(
                f"{_employee_name(owner)} ({shift_text(shift)}) v tom čase "
                f"uviedol, že nemôže pracovať: " + "; ".join(conflicts)
            )

    return True, warnings


# ---------------------------------------------------------------------
# Čítanie
# ---------------------------------------------------------------------

def _swap_query():
    proposer = aliased(Employee)
    colleague = aliased(Employee)
    decider = User.__table__.alias("decider")

    return (
        select(
            ShiftSwap.id,                                        # 0
            ShiftSwap.proposer_employee_id,                      # 1
            proposer.first_name,                                 # 2
            proposer.last_name,                                  # 3
            ShiftSwap.colleague_employee_id,                     # 4
            colleague.first_name,                                # 5
            colleague.last_name,                                 # 6
            proposer.branch_id,                                  # 7
            Branch.name,                                         # 8
            ShiftSwap.offered_text,                              # 9
            ShiftSwap.counter_text,                              # 10
            ShiftSwap.wants_swap,                                # 11
            ShiftSwap.note,                                      # 12
            ShiftSwap.status,                                    # 13
            ShiftSwap.created_at,                                # 14
            ShiftSwap.colleague_responded_at,                    # 15
            ShiftSwap.decided_at,                                # 16
            decider.c.username,                                  # 17
            ShiftSwap.decision_note,                             # 18
        )
        .join(proposer, ShiftSwap.proposer_employee_id == proposer.id)
        .join(colleague, ShiftSwap.colleague_employee_id == colleague.id)
        .join(Branch, proposer.branch_id == Branch.id)
        .outerjoin(decider, ShiftSwap.decided_by_user_id == decider.c.id)
    ), proposer


def get_swaps(branch_ids=None, status=None, employee_id=None):
    """Žiadosti ako riadky (id, navrhovateľ id, meno, priezvisko,
    kolega id, meno, priezvisko, branch_id, pobočka, ponúkaná smena,
    smena na oplátku, výmena?, poznámka, stav, vytvorená, odpoveď
    kolegu, rozhodnutá, kto rozhodol, poznámka k rozhodnutiu).
    ``employee_id`` vráti žiadosti, kde je zamestnanec navrhovateľom
    alebo kolegom."""

    query, proposer = _swap_query()

    if branch_ids is not None:
        query = query.where(proposer.branch_id.in_(list(branch_ids)))

    if status:
        query = query.where(ShiftSwap.status == status)

    if employee_id is not None:
        query = query.where(
            or_(
                ShiftSwap.proposer_employee_id == employee_id,
                ShiftSwap.colleague_employee_id == employee_id,
            )
        )

    return db.session.execute(
        query.order_by(ShiftSwap.created_at.desc(), ShiftSwap.id.desc())
    ).all()


def get_swap(swap_id):
    query, _ = _swap_query()

    return db.session.execute(query.where(ShiftSwap.id == swap_id)).first()


def pending_manager_count(branch_ids=None):
    query = (
        select(func.count(ShiftSwap.id))
        .join(Employee, ShiftSwap.proposer_employee_id == Employee.id)
        .where(ShiftSwap.status == STATUS_PENDING_MANAGER)
    )

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    return db.session.execute(query).scalar() or 0


def incoming_count(employee_id):
    """Koľko žiadostí čaká na odpoveď tohto zamestnanca (kolegu)."""

    return db.session.execute(
        select(func.count(ShiftSwap.id)).where(
            ShiftSwap.colleague_employee_id == employee_id,
            ShiftSwap.status == STATUS_PENDING_COLLEAGUE,
        )
    ).scalar() or 0
