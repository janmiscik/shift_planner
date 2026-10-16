"""Služby pre prácu so zamestnancami.

Riadky zamestnanca (``get_employee`` / ``get_employees``) majú tvar:
(id, meno, priezvisko, pozícia, týždenný fond, aktívny, branch_id,
názov pobočky).
"""

from sqlalchemy import select

from app.extensions import db
from app.orm_models import Branch, Employee


def add_employee(
    first_name, last_name, position, weekly_hours, branch_id=None
):
    """Pridá zamestnanca do databázy (do danej pobočky, inak do
    predvolenej)."""

    from app.services.branch_service import get_default_branch_id

    if branch_id is None:
        branch_id = get_default_branch_id()

    employee = Employee(
        first_name=first_name,
        last_name=last_name,
        position=position,
        employment_type="custom",
        weekly_hours=weekly_hours,
        branch_id=branch_id,
    )

    db.session.add(employee)
    db.session.commit()

    return employee.id


def _employee_columns():
    return (
        Employee.id,
        Employee.first_name,
        Employee.last_name,
        Employee.position,
        Employee.weekly_hours,
        Employee.active,
        Employee.branch_id,
        Branch.name,
    )


def get_employees(branch_ids=None):
    """Načíta zamestnancov. ``branch_ids=None`` = všetky pobočky,
    zoznam obmedzí výber na dané pobočky."""

    query = (
        select(*_employee_columns())
        .join(Branch, Employee.branch_id == Branch.id)
        .order_by(Employee.last_name, Employee.first_name)
    )

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    return db.session.execute(query).all()


def get_employee(employee_id):
    """Načíta jedného zamestnanca."""

    query = (
        select(*_employee_columns())
        .join(Branch, Employee.branch_id == Branch.id)
        .where(Employee.id == employee_id)
    )

    return db.session.execute(query).first()


def update_employee(
    employee_id,
    first_name,
    last_name,
    position,
    weekly_hours,
):
    """Upraví údaje zamestnanca (pobočka sa nemení)."""

    employee = db.session.get(Employee, employee_id)

    if employee is None:
        return

    employee.first_name = first_name
    employee.last_name = last_name
    employee.position = position
    employee.weekly_hours = weekly_hours

    db.session.commit()


def set_employee_active(employee_id, active):
    """Aktivuje alebo deaktivuje zamestnanca."""

    employee = db.session.get(Employee, employee_id)

    if employee is None:
        return

    employee.active = active

    db.session.commit()
