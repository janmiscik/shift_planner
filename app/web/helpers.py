"""Spoločné pomocné funkcie routov (rozsah pobočiek, voľby vo formulároch)."""

from flask import (
    request,
)
from flask_login import (
    current_user,
)
from app.services.access_service import (
    accessible_branch_ids,
    employee_branch_id,
    resolve_scope,
)
from app.services.branch_service import (
    get_branches,
    get_default_branch_id,
)
from app.services.employee_service import (
    get_employees,
)
from app.services.department_service import (
    get_departments,
)


def _scope():
    """Rozsah pobočiek pre zoznamy a exporty: celý prístupný rozsah,
    alebo jedna pobočka vybraná cez ``?branch_id=`` (ak na ňu
    používateľ má právo). ``None`` = bez obmedzenia (admin)."""

    return resolve_scope(
        current_user, request.args.get("branch_id", type=int)
    )


def _selected_branch_id():
    """Pobočka vybraná cez ``?branch_id=``, ak je platná a prístupná
    (inak ``None`` = všetky prístupné)."""

    requested = request.args.get("branch_id", type=int)

    if requested is None:
        return None

    allowed = accessible_branch_ids(current_user)

    if allowed is None or requested in allowed:
        return requested

    return None


def _accessible_branches():
    """Pobočky, ktoré smie používateľ vidieť (pre výber v UI)."""

    return get_branches(accessible_branch_ids(current_user))


def _branch_in_access(branch_id):
    allowed = accessible_branch_ids(current_user)

    return allowed is None or branch_id in allowed


def _employee_in_access(employee_id):
    return _branch_in_access(employee_branch_id(employee_id))


def _active_department_choices(branch_ids=None):
    return [
        (department[0], department[1])
        for department in get_departments(branch_ids)
        if department[2]
    ]


def _active_employee_choices(branch_ids=None):
    return [
        (employee[0], f"{employee[1]} {employee[2]} — {employee[3]}")
        for employee in get_employees(branch_ids)
        if employee[5]
    ]


def _branch_form_choices():
    """Voľby pre výber pobočky vo formulároch (len aktívne pobočky;
    prázdna hodnota = predvolená pobočka)."""

    branches = get_branches(only_active=True)

    return branches, [("", "")] + [
        (str(branch[0]), branch[1]) for branch in branches
    ]


def _resolve_branch_id(raw_value):
    if raw_value:
        return int(raw_value)

    return get_default_branch_id()
