"""Služby pre správu neprítomností (dovolenka, PN, OČR, náhradné voľno).

Kalendár zmien pri validácii smeny (``shift_service.validate_shift``)
automaticky zohľadňuje schválené neprítomnosti - zamestnancovi sa
nedá naplánovať smena na deň, kedy má neprítomnosť.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.extensions import db
from app.orm_models import Absence, Employee

ABSENCE_TYPES = ["Dovolenka", "PN", "OČR", "Náhradné voľno"]


def add_absence(employee_id, absence_type, start_date, end_date, note=None):
    """Pridá neprítomnosť zamestnancovi."""

    absence = Absence(
        employee_id=employee_id,
        absence_type=absence_type,
        start_date=start_date,
        end_date=end_date,
        note=note or None,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )

    db.session.add(absence)
    db.session.commit()

    return absence.id


def _absence_columns():
    return (
        Absence.id,
        Employee.first_name,
        Employee.last_name,
        Absence.employee_id,
        Absence.absence_type,
        Absence.start_date,
        Absence.end_date,
        Absence.note,
    )


def get_absences():
    """Načíta všetky neprítomnosti spolu so zamestnancom."""

    query = (
        select(*_absence_columns())
        .join(Employee, Absence.employee_id == Employee.id)
        .order_by(Absence.start_date.desc())
    )

    return db.session.execute(query).all()


def get_absences_by_employee(employee_id):
    """Načíta neprítomnosti konkrétneho zamestnanca."""

    query = (
        select(*_absence_columns())
        .join(Employee, Absence.employee_id == Employee.id)
        .where(Absence.employee_id == employee_id)
        .order_by(Absence.start_date.desc())
    )

    return db.session.execute(query).all()


def get_absence(absence_id):
    """Načíta jednu neprítomnosť."""

    query = select(
        Absence.id,
        Absence.employee_id,
        Absence.absence_type,
        Absence.start_date,
        Absence.end_date,
        Absence.note,
    ).where(Absence.id == absence_id)

    return db.session.execute(query).first()


def update_absence(absence_id, absence_type, start_date, end_date, note=None):
    """Upraví existujúcu neprítomnosť."""

    absence = db.session.get(Absence, absence_id)

    if absence is None:
        return

    absence.absence_type = absence_type
    absence.start_date = start_date
    absence.end_date = end_date
    absence.note = note or None

    db.session.commit()


def delete_absence(absence_id):
    """Vymaže neprítomnosť."""

    absence = db.session.get(Absence, absence_id)

    if absence is None:
        return

    db.session.delete(absence)
    db.session.commit()


def get_employee_absence_on_date(employee_id, check_date):
    """Vráti neprítomnosť zamestnanca na daný deň, ak nejaká je
    (inak ``None``). ``check_date`` je ISO reťazec "YYYY-MM-DD"."""

    query = select(
        Absence.id,
        Absence.absence_type,
        Absence.start_date,
        Absence.end_date,
    ).where(
        Absence.employee_id == employee_id,
        Absence.start_date <= check_date,
        Absence.end_date >= check_date,
    )

    return db.session.execute(query).first()


def get_absences_in_range(start_date, end_date):
    """Načíta neprítomnosti prekrývajúce sa s daným rozsahom dátumov
    (napr. pre zobrazenie v kalendári)."""

    query = (
        select(*_absence_columns())
        .join(Employee, Absence.employee_id == Employee.id)
        .where(
            Absence.start_date <= end_date,
            Absence.end_date >= start_date,
        )
        .order_by(Absence.start_date)
    )

    return db.session.execute(query).all()
