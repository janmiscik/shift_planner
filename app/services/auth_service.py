"""Služby pre prihlasovacie účty.

Heslá sa ukladajú len ako hash (``werkzeug.security`` - súčasť
Flasku, žiadna nová závislosť), nikdy v čitateľnej podobe.
"""

from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.orm_models import User


def username_exists(username, exclude_user_id=None):
    """Overí, či už existuje účet s daným používateľským menom
    (bez ohľadu na veľkosť písmen)."""

    query = select(User.id).where(func.lower(User.username) == func.lower(username))

    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)

    return db.session.execute(query).first() is not None


def create_manager(username, password):
    """Vytvorí účet manažéra (plný prístup). Vráti ``None``, ak
    používateľské meno už existuje."""

    if username_exists(username):
        return None

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role="manager",
        employee_id=None,
    )

    db.session.add(user)
    db.session.commit()

    return user.id


def create_employee_user(employee_id, username, password):
    """Vytvorí prihlasovací účet naviazaný na konkrétneho
    zamestnanca (prístup len na čítanie vlastného rozpisu). Vráti
    ``None``, ak používateľské meno už existuje."""

    if username_exists(username):
        return None

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role="employee",
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


def any_manager_exists():
    """Overí, či existuje aspoň jeden manažérsky účet (napr. pre
    kontrolu pri prvom spustení appky)."""

    query = select(User.id).where(User.role == "manager")

    return db.session.execute(query).first() is not None
