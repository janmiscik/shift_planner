"""Služby pre prihlasovacie účty.

Heslá sa ukladajú len ako hash (``werkzeug.security`` - súčasť
Flasku, žiadna nová závislosť), nikdy v čitateľnej podobe.
"""

from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.orm_models import (
    Branch,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    User,
)


def username_exists(username, exclude_user_id=None):
    """Overí, či už existuje účet s daným používateľským menom
    (bez ohľadu na veľkosť písmen)."""

    query = select(User.id).where(func.lower(User.username) == func.lower(username))

    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)

    return db.session.execute(query).first() is not None


def create_admin(username, password):
    """Vytvorí účet administrátora (plný prístup ku všetkému). Vráti
    ``None``, ak používateľské meno už existuje."""

    if username_exists(username):
        return None

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role=ROLE_ADMIN,
        employee_id=None,
    )

    db.session.add(user)
    db.session.commit()

    return user.id


def create_manager(username, password, branch_ids=()):
    """Vytvorí účet manažéra - vytvára smeny a absencie, ale len na
    pridelených pobočkách (``branch_ids``). Vráti ``None``, ak
    používateľské meno už existuje."""

    if username_exists(username):
        return None

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role=ROLE_MANAGER,
        employee_id=None,
    )
    user.branches = _branches_by_ids(branch_ids)

    db.session.add(user)
    db.session.commit()

    return user.id


def _branches_by_ids(branch_ids):
    branch_ids = {int(branch_id) for branch_id in branch_ids}

    if not branch_ids:
        return []

    return list(
        db.session.execute(
            select(Branch).where(Branch.id.in_(branch_ids))
        ).scalars()
    )


def set_user_branches(user_id, branch_ids):
    """Nastaví pobočky, ktoré manažér smie spravovať (prepíše
    pôvodné). Vráti ``False``, ak účet neexistuje."""

    user = db.session.get(User, user_id)

    if user is None:
        return False

    user.branches = _branches_by_ids(branch_ids)

    db.session.commit()

    return True


def set_manager_function(employee_id, enabled, branch_ids=()):
    """Pridelí/odoberie funkciu manažéra zamestnancovi, ktorý už má
    prihlasovací účet - ostáva JEDEN účet, viazaný na zamestnanca.

    ``enabled=True``: účet sa stane manažérskym (vytvára smeny a
    absencie v ``branch_ids``) a zamestnanec si popri tom naďalej
    pozerá vlastný rozpis. ``enabled=False``: účet je opäť obyčajný
    zamestnanecký a pridelené pobočky sa zrušia.

    Vráti ``False``, ak zamestnanec nemá účet.
    """

    user = db.session.execute(
        select(User).where(User.employee_id == employee_id)
    ).scalar_one_or_none()

    if user is None or user.role == ROLE_ADMIN:
        return False

    if enabled:
        user.role = ROLE_MANAGER
        user.branches = _branches_by_ids(branch_ids)
    else:
        user.role = ROLE_EMPLOYEE
        user.branches = []

    db.session.commit()

    return True


def get_staff_users():
    """Účty admin a manažér (účty zamestnancov sa spravujú na stránke
    zamestnanca) ako ORM objekty zoradené podľa mena."""

    return list(
        db.session.execute(
            select(User)
            .where(User.role.in_([ROLE_ADMIN, ROLE_MANAGER]))
            .order_by(func.lower(User.username))
        ).scalars()
    )


def count_admins():
    return db.session.execute(
        select(func.count(User.id)).where(User.role == ROLE_ADMIN)
    ).scalar()


def create_employee_user(employee_id, username, password):
    """Vytvorí prihlasovací účet naviazaný na konkrétneho
    zamestnanca (prístup len na čítanie vlastného rozpisu). Vráti
    ``None``, ak používateľské meno už existuje."""

    if username_exists(username):
        return None

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role=ROLE_EMPLOYEE,
        employee_id=employee_id,
    )

    db.session.add(user)
    db.session.commit()

    return user.id


def get_user_by_username(username):
    """Načíta účet podľa používateľského mena (case-insensitive)."""

    query = select(User).where(func.lower(User.username) == func.lower(username))

    return db.session.execute(query).scalar_one_or_none()


def get_user_by_id(user_id):
    """Načíta účet podľa ID (používa Flask-Login pri obnove session)."""

    return db.session.get(User, int(user_id))


def get_user_by_employee_id(employee_id):
    """Načíta prihlasovací účet naviazaný na zamestnanca, ak nejaký
    existuje."""

    query = select(User).where(User.employee_id == employee_id)

    return db.session.execute(query).scalar_one_or_none()


def verify_password(user, password):
    """Overí heslo voči uloženému hashu."""

    return check_password_hash(user.password_hash, password)


def set_password(user_id, new_password):
    """Nastaví nové heslo existujúcemu účtu (napr. reset manažérom)."""

    user = db.session.get(User, user_id)

    if user is None:
        return False

    user.password_hash = generate_password_hash(new_password)

    db.session.commit()

    return True


def set_username(user_id, new_username):
    """Zmení používateľské meno existujúceho účtu.

    Vráti ``False``, ak je nové meno už obsadené iným účtom.
    """

    if username_exists(new_username, exclude_user_id=user_id):
        return False

    user = db.session.get(User, user_id)

    if user is None:
        return False

    user.username = new_username

    db.session.commit()

    return True


def delete_user(user_id):
    """Zruší prihlasovací účet."""

    user = db.session.get(User, user_id)

    if user is None:
        return

    db.session.delete(user)
    db.session.commit()


def any_admin_exists():
    """Overí, či existuje aspoň jeden administrátorský účet (napr. pre
    kontrolu pri prvom spustení appky)."""

    query = select(User.id).where(User.role == ROLE_ADMIN)

    return db.session.execute(query).first() is not None


def change_own_password(user_id, current_password, new_password):
    """Zmena hesla samotným používateľom (vyžaduje aktuálne heslo).
    Vráti ``False``, ak aktuálne heslo nesedí."""

    user = db.session.get(User, user_id)

    if user is None or not check_password_hash(
        user.password_hash, current_password
    ):
        return False

    user.password_hash = generate_password_hash(new_password)

    db.session.commit()

    return True
