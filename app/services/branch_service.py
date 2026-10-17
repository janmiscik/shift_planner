"""Služby pre pobočky."""

from sqlalchemy import func, select

from app.extensions import db
from app.orm_models import Branch, Department, Employee
from app.services.text_utils import same_name

DEFAULT_BRANCH_NAME = "Hlavná pobočka"


def branch_name_exists(name, exclude_branch_id=None):
    """Overí, či už existuje pobočka s daným názvom (bez ohľadu na
    veľkosť písmen aj diakritiku písmen typu Š/š)."""

    rows = db.session.execute(select(Branch.id, Branch.name)).all()

    return any(
        same_name(existing_name, name) and branch_id != exclude_branch_id
        for branch_id, existing_name in rows
    )


def add_branch(name):
    """Pridá pobočku. Vráti ``None``, ak názov už existuje."""

    if branch_name_exists(name):
        return None

    branch = Branch(name=name, active=1)

    db.session.add(branch)
    db.session.commit()

    return branch.id


def get_branches(branch_ids=None, only_active=False):
    """Načíta pobočky ako (id, názov, aktívna).

    ``branch_ids=None`` znamená všetky pobočky, zoznam obmedzí výber
    len na dané pobočky (napr. pobočky manažéra).
    """

    query = select(Branch.id, Branch.name, Branch.active).order_by(
        Branch.name
    )

    if branch_ids is not None:
        query = query.where(Branch.id.in_(list(branch_ids)))

    if only_active:
        query = query.where(Branch.active == 1)

    return db.session.execute(query).all()


def get_branches_with_counts():
    """Pobočky + počet zamestnancov a oddelení:
    (id, názov, aktívna, počet_zamestnancov, počet_oddelení)."""

    employee_counts = dict(
        db.session.execute(
            select(Employee.branch_id, func.count(Employee.id)).group_by(
                Employee.branch_id
            )
        ).all()
    )
    department_counts = dict(
        db.session.execute(
            select(Department.branch_id, func.count(Department.id)).group_by(
                Department.branch_id
            )
        ).all()
    )

    return [
        (
            branch_id,
            name,
            active,
            employee_counts.get(branch_id, 0),
            department_counts.get(branch_id, 0),
        )
        for branch_id, name, active in get_branches()
    ]


def get_branch(branch_id):
    """Načíta jednu pobočku ako (id, názov, aktívna)."""

    query = select(Branch.id, Branch.name, Branch.active).where(
        Branch.id == branch_id
    )

    return db.session.execute(query).first()


def update_branch(branch_id, name):
    """Premenuje pobočku. Vráti ``False``, ak názov už používa iná
    pobočka alebo pobočka neexistuje."""

    if branch_name_exists(name, exclude_branch_id=branch_id):
        return False

    branch = db.session.get(Branch, branch_id)

    if branch is None:
        return False

    branch.name = name

    db.session.commit()

    return True


def set_branch_active(branch_id, active):
    """Aktivuje/deaktivuje pobočku (pobočky sa nemažú - viazali by sa
    na ne zamestnanci, oddelenia a smeny)."""

    branch = db.session.get(Branch, branch_id)

    if branch is None:
        return

    branch.active = active

    db.session.commit()


def get_default_branch_id():
    """Vráti ID predvolenej pobočky (aktívna pobočka s najnižším ID).

    Ak v databáze nie je ešte žiadna pobočka (napr. úplne nová
    inštalácia alebo testovacia DB), vytvorí sa "Hlavná pobočka".
    Vráti ``None``, len ak pobočky existujú, ale žiadna nie je
    aktívna.
    """

    existing = db.session.execute(select(func.count(Branch.id))).scalar()

    if not existing:
        return add_branch(DEFAULT_BRANCH_NAME)

    return db.session.execute(
        select(Branch.id).where(Branch.active == 1).order_by(Branch.id)
    ).scalar()
