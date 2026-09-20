"""Služby pre prácu so zamestnancami."""

from sqlalchemy import select

from app.extensions import db
from app.orm_models import Employee


def add_employee(first_name, last_name, position, weekly_hours):
    """Pridá zamestnanca do databázy."""

    employee = Employee(
        first_name=first_name,
        last_name=last_name,
        position=position,
        employment_type="custom",
        weekly_hours=weekly_hours,
    )

    db.session.add(employee)
    db.session.commit()

    return employee.id


def get_employees():
    """Načíta všetkých zamestnancov."""

    query = select(
        Employee.id,
        Employee.first_name,
        Employee.last_name,
        Employee.position,
        Employee.weekly_hours,
        Employee.active,
    ).order_by(Employee.last_name, Employee.first_name)

    return db.session.execute(query).all()


def get_employee(employee_id):
    """Načíta jedného zamestnanca."""

    query = select(
        Employee.id,
        Employee.first_name,
        Employee.last_name,
        Employee.position,
        Employee.weekly_hours,
        Employee.active,
    ).where(Employee.id == employee_id)

    return db.session.execute(query).first()


def update_employee(
    employee_id,
    first_name,
    last_name,
    position,
    weekly_hours,
):
    """Upraví údaje zamestnanca."""

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
