"""Služby pre prácu s oddeleniami."""

from sqlalchemy import func, select

from app.extensions import db
from app.orm_models import Department


def department_name_exists(name, exclude_department_id=None):
    """Overí, či už existuje oddelenie s daným názvom (bez ohľadu na
    veľkosť písmen)."""

    query = select(Department.id).where(
        func.lower(Department.name) == func.lower(name)
    )

    if exclude_department_id is not None:
        query = query.where(Department.id != exclude_department_id)

    return db.session.execute(query).first() is not None


def add_department(name, min_staff=None):
    """Pridá nové oddelenie.

    Vráti ``None``, ak už oddelenie s týmto názvom existuje (namiesto
    pádu na UNIQUE obmedzení v databáze).
    """

    if department_name_exists(name):
        return None

    department = Department(name=name, min_staff=min_staff)

    db.session.add(department)
    db.session.commit()

    return department.id


def get_departments():
    """Načíta všetky oddelenia."""

    query = select(
        Department.id,
        Department.name,
        Department.active,
        Department.min_staff,
    ).order_by(Department.name)

    return db.session.execute(query).all()


def get_department(department_id):
    """Načíta jedno oddelenie."""

    query = select(
        Department.id,
        Department.name,
        Department.active,
        Department.min_staff,
    ).where(Department.id == department_id)

    return db.session.execute(query).first()


def update_department(department_id, name, min_staff=None):
    """Upraví názov a minimálny počet ľudí na zmene pre oddelenie.

    Vráti ``False``, ak už iné oddelenie s týmto názvom existuje.
    """

    if department_name_exists(name, exclude_department_id=department_id):
        return False

    department = db.session.get(Department, department_id)

    if department is None:
        return False

    department.name = name
    department.min_staff = min_staff

    db.session.commit()

    return True


def set_department_active(department_id, active):
    """Aktivuje alebo deaktivuje oddelenie."""

    department = db.session.get(Department, department_id)

    if department is None:
        return

    department.active = active

    db.session.commit()
