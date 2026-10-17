"""Služby pre prácu s oddeleniami.

Riadky oddelenia (``get_department`` / ``get_departments``) majú tvar:
(id, názov, aktívne, min_staff, branch_id, názov pobočky).
Názov oddelenia je jedinečný v rámci pobočky.
"""

from sqlalchemy import select

from app.extensions import db
from app.orm_models import Branch, Department
from app.services.text_utils import same_name


def department_name_exists(name, branch_id, exclude_department_id=None):
    """Overí, či už v danej pobočke existuje oddelenie s daným názvom
    (bez ohľadu na veľkosť písmen aj diakritiku písmen typu Š/š)."""

    rows = db.session.execute(
        select(Department.id, Department.name).where(
            Department.branch_id == branch_id
        )
    ).all()

    return any(
        same_name(existing_name, name)
        and department_id != exclude_department_id
        for department_id, existing_name in rows
    )


def add_department(name, min_staff=None, branch_id=None):
    """Pridá nové oddelenie do pobočky (inak do predvolenej).

    Vráti ``None``, ak už oddelenie s týmto názvom v pobočke existuje
    (namiesto pádu na UNIQUE obmedzení v databáze).
    """

    from app.services.branch_service import get_default_branch_id

    if branch_id is None:
        branch_id = get_default_branch_id()

    if department_name_exists(name, branch_id):
        return None

    department = Department(
        name=name, min_staff=min_staff, branch_id=branch_id
    )

    db.session.add(department)
    db.session.commit()

    return department.id


def _department_columns():
    return (
        Department.id,
        Department.name,
        Department.active,
        Department.min_staff,
        Department.branch_id,
        Branch.name,
    )


def get_departments(branch_ids=None):
    """Načíta oddelenia. ``branch_ids=None`` = všetky pobočky, zoznam
    obmedzí výber na dané pobočky."""

    query = (
        select(*_department_columns())
        .join(Branch, Department.branch_id == Branch.id)
        .order_by(Branch.name, Department.name)
    )

    if branch_ids is not None:
        query = query.where(Department.branch_id.in_(list(branch_ids)))

    return db.session.execute(query).all()


def get_department(department_id):
    """Načíta jedno oddelenie."""

    query = (
        select(*_department_columns())
        .join(Branch, Department.branch_id == Branch.id)
        .where(Department.id == department_id)
    )

    return db.session.execute(query).first()


def update_department(department_id, name, min_staff=None):
    """Upraví názov a minimálny počet ľudí na zmene pre oddelenie.

    Vráti ``False``, ak už iné oddelenie v tej istej pobočke s týmto
    názvom existuje. Pobočka oddelenia sa nemení.
    """

    department = db.session.get(Department, department_id)

    if department is None:
        return False

    if department_name_exists(
        name,
        department.branch_id,
        exclude_department_id=department_id,
    ):
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
