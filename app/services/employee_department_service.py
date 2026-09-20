"""Služby pre priradenie zamestnancov k oddeleniam (M:N vzťah).

Vďaka SQLAlchemy vzťahom (``Employee.department_links``,
``association_proxy``) sa dá k oddeleniam zamestnanca pristupovať
priamo cez ``employee.departments`` bez ručného JOIN-u - využíva to
napr. ``get_employee_department_ids``.
"""

from sqlalchemy import func, select

from app.extensions import db
from app.orm_models import Department, Employee, EmployeeDepartment


def add_employee_department(employee_id, department_id, weekly_hours):
    """Priradí zamestnanca k oddeleniu.

    Vráti ``None``, ak už priradenie existuje (namiesto pádu na
    UNIQUE obmedzení).
    """

    existing = db.session.execute(
        select(EmployeeDepartment.id).where(
            EmployeeDepartment.employee_id == employee_id,
            EmployeeDepartment.department_id == department_id,
        )
    ).first()

    if existing:
        return None

    assignment = EmployeeDepartment(
        employee_id=employee_id,
        department_id=department_id,
        weekly_hours=weekly_hours,
    )

    db.session.add(assignment)
    db.session.commit()

    return assignment.id


def get_employee_departments(employee_id):
    """Načíta oddelenia konkrétneho zamestnanca."""

    query = (
        select(
            EmployeeDepartment.id,
            Department.id,
            Department.name,
            EmployeeDepartment.weekly_hours,
        )
        .join(Department, EmployeeDepartment.department_id == Department.id)
        .where(EmployeeDepartment.employee_id == employee_id)
        .order_by(Department.name)
    )

    return db.session.execute(query).all()


def get_employee_department_ids(employee_id):
    """Vráti množinu ID oddelení, ku ktorým je zamestnanec priradený."""

    employee = db.session.get(Employee, employee_id)

    if employee is None:
        return set()

    return {department.id for department in employee.departments}


def get_employee_department(assignment_id):
    """Načíta jedno priradenie."""

    query = select(
        EmployeeDepartment.id,
        EmployeeDepartment.employee_id,
        EmployeeDepartment.department_id,
        EmployeeDepartment.weekly_hours,
    ).where(EmployeeDepartment.id == assignment_id)

    return db.session.execute(query).first()


def update_employee_department(assignment_id, department_id, weekly_hours):
    """Upraví priradenie zamestnanca."""

    assignment = db.session.get(EmployeeDepartment, assignment_id)

    if assignment is None:
        return

    assignment.department_id = department_id
    assignment.weekly_hours = weekly_hours

    db.session.commit()


def delete_employee_department(assignment_id):
    """Odstráni priradenie zamestnanca k oddeleniu."""

    assignment = db.session.get(EmployeeDepartment, assignment_id)

    if assignment is None:
        return

    db.session.delete(assignment)
    db.session.commit()


def get_employee_department_hours(employee_id):
    """Vráti celkový počet hodín podľa oddelení."""

    query = select(
        func.coalesce(func.sum(EmployeeDepartment.weekly_hours), 0)
    ).where(EmployeeDepartment.employee_id == employee_id)

    return db.session.execute(query).scalar()


def can_add_employee_department(employee_id, weekly_hours):
    """Overí, či nové priradenie neprekročí pracovný fond."""

    employee = db.session.get(Employee, employee_id)

    if employee is None or employee.weekly_hours is None:
        return False

    current_hours = get_employee_department_hours(employee_id)

    return current_hours + float(weekly_hours) <= float(employee.weekly_hours)


def can_update_employee_department(employee_id, assignment_id, weekly_hours):
    """Overí, či úprava priradenia neprekročí pracovný fond."""

    employee = db.session.get(Employee, employee_id)

    if employee is None or employee.weekly_hours is None:
        return False

    query = select(
        func.coalesce(func.sum(EmployeeDepartment.weekly_hours), 0)
    ).where(
        EmployeeDepartment.employee_id == employee_id,
        EmployeeDepartment.id != assignment_id,
    )

    other_hours = db.session.execute(query).scalar()

    return other_hours + float(weekly_hours) <= float(employee.weekly_hours)
