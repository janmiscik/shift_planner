"""Oprávnenia - ku ktorým pobočkám má prihlásený používateľ prístup.

Základná myšlienka: každý dotaz na zamestnancov, oddelenia, smeny a
absencie dostane ``branch_ids``:
  - ``None``  -> bez obmedzenia (admin),
  - zoznam ID -> len dané pobočky (manažér: pridelené pobočky,
                 zamestnanec: pobočka, v ktorej pracuje).
Prázdny zoznam znamená "žiadny prístup" (nič sa nevráti).
"""

from sqlalchemy import select

from app.extensions import db
from app.orm_models import (
    Absence,
    Department,
    Employee,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    Shift,
)


def accessible_branch_ids(user):
    """Vráti ``None`` (admin - všetko) alebo zoznam ID pobočiek, ku
    ktorým má používateľ prístup."""

    if user is None or not getattr(user, "is_authenticated", False):
        return []

    if user.role == ROLE_ADMIN:
        return None

    if user.role == ROLE_MANAGER:
        return sorted(branch.id for branch in user.branches)

    if user.role == ROLE_EMPLOYEE:
        if user.employee is None:
            return []

        return [user.employee.branch_id]

    return []


def resolve_scope(user, requested_branch_id=None):
    """Rozsah pobočiek pre zoznamy/exporty.

    Ak používateľ vybral konkrétnu pobočku (``?branch_id=``) a smie ju
    vidieť, vráti sa len ona. Neplatný/nepovolený výber sa ignoruje
    (vráti sa celý prístupný rozsah).
    """

    allowed = accessible_branch_ids(user)

    if requested_branch_id is None:
        return allowed

    if allowed is None or requested_branch_id in allowed:
        return [requested_branch_id]

    return allowed


def branch_in_scope(branch_id, scope):
    return scope is None or branch_id in scope


def employee_branch_id(employee_id):
    return db.session.execute(
        select(Employee.branch_id).where(Employee.id == employee_id)
    ).scalar()


def department_branch_id(department_id):
    return db.session.execute(
        select(Department.branch_id).where(Department.id == department_id)
    ).scalar()


def shift_branch_id(shift_id):
    """Pobočka smeny = pobočka jej zamestnanca."""

    return db.session.execute(
        select(Employee.branch_id)
        .join(Shift, Shift.employee_id == Employee.id)
        .where(Shift.id == shift_id)
    ).scalar()


def absence_branch_id(absence_id):
    return db.session.execute(
        select(Employee.branch_id)
        .join(Absence, Absence.employee_id == Employee.id)
        .where(Absence.id == absence_id)
    ).scalar()
