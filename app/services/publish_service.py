"""Zverejňovanie rozpisu (koncept vs. zverejnené).

Nové smeny sú predvolene KONCEPT - vidí ich len manažér/admin.
Zamestnanec vidí (vlastný rozpis, exporty, kalendár pobočky) len
ZVEREJNENÉ smeny. Manažér zverejňuje po týždňoch a pobočkách
(``publish_week``), prípadne zverejnenie stiahne (``unpublish_week``).

Úprava alebo presun už zverejnenej smeny príznak zachová - zamestnanec
vidí zmenu hneď (inak by mu smena z rozpisu zmizla). Smena pridaná do
už zverejneného týždňa je opäť koncept, kým sa týždeň nezverejní
znova. ``AUTO_PUBLISH_SHIFTS=1`` zapne zverejňovanie nových smien hneď.
"""

from datetime import timedelta

from sqlalchemy import select, update

from app.extensions import db
from app.orm_models import Branch, Employee, Shift
from app.services.audit_service import record_event


def _week_filter(monday, branch_id):
    sunday = monday + timedelta(days=6)

    return (
        Shift.shift_date.between(monday.isoformat(), sunday.isoformat()),
        Employee.branch_id == branch_id,
    )


def get_draft_shift_ids(start_date=None, end_date=None, branch_ids=None):
    """Množina ID smien v koncepte (voliteľne len v dátumovom rozsahu a
    daných pobočkách)."""

    query = (
        select(Shift.id)
        .join(Employee, Shift.employee_id == Employee.id)
        .where(Shift.published == 0)
    )

    if start_date is not None and end_date is not None:
        query = query.where(Shift.shift_date.between(start_date, end_date))

    if branch_ids is not None:
        query = query.where(Employee.branch_id.in_(list(branch_ids)))

    return set(db.session.execute(query).scalars())


def week_publication_summary(monday, branch_id):
    """Koľko smien v týždni a pobočke je v koncepte / zverejnených."""

    rows = db.session.execute(
        select(Shift.published)
        .join(Employee, Shift.employee_id == Employee.id)
        .where(*_week_filter(monday, branch_id))
    ).scalars().all()

    drafts = sum(1 for value in rows if not value)

    return {
        "total": len(rows),
        "drafts": drafts,
        "published": len(rows) - drafts,
    }


def _set_week_published(monday, branch_id, published):
    shift_ids = db.session.execute(
        select(Shift.id)
        .join(Employee, Shift.employee_id == Employee.id)
        .where(
            *_week_filter(monday, branch_id),
            Shift.published == (0 if published else 1),
        )
    ).scalars().all()

    if shift_ids:
        db.session.execute(
            update(Shift)
            .where(Shift.id.in_(shift_ids))
            .values(published=1 if published else 0)
        )
        db.session.commit()

        # Hromadný SQL UPDATE ORM udalosti nevidia - zapíšeme výslovne.
        branch = db.session.get(Branch, branch_id)
        sunday = monday + timedelta(days=6)

        record_event(
            "shift.publish" if published else "shift.unpublish",
            (
                f"{'Zverejnený' if published else 'Stiahnutý zo zverejnenia'}"
                f" rozpis {monday.isoformat()} – {sunday.isoformat()}, "
                f"pobočka {branch.name if branch else branch_id}: "
                f"{len(shift_ids)} smien"
            ),
            "week",
            branch_id=branch_id,
        )

        if published:
            from app.services.notification_service import (
                notify_schedule_published,
            )

            notify_schedule_published(shift_ids, monday, branch_id)

    return len(shift_ids)


def publish_week(monday, branch_id):
    """Zverejní všetky koncepty v týždni a pobočke. Vráti ich počet."""

    return _set_week_published(monday, branch_id, True)


def unpublish_week(monday, branch_id):
    """Stiahne zverejnené smeny týždňa späť do konceptu. Vráti počet."""

    return _set_week_published(monday, branch_id, False)
