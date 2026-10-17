"""Plánovanie týždňa: kopírovanie týždňa a šablóny smien.

Spoločné jadro je ``run_plan``: zoberie zoznam smien jedného týždňa
(``PlanEntry``) a vytvorí ich v cieľovom týždni. Každá smena prejde
rovnakými pravidlami ako pri ručnom pridaní (``validate_shift``:
prekrytie, odpočinok, týždenné fondy, absencie, oddelenie). Smena, ktorá
by pravidlá porušila, sa PRESKOČÍ a uvedie sa dôvod - ostatné sa vytvoria.

Náhľad (``commit=False``) smeny vloží do transakcie (aby sa pravidlá
počítali aj medzi smenami z toho istého kopírovania, napr. súčet hodín
za týždeň) a nakoniec ju vráti späť - nič sa nezapíše.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.extensions import db
from app.orm_models import (
    Department,
    Employee,
    Shift,
    ShiftTemplate,
    ShiftTemplateEntry,
)
from app.services.employee_service import get_employee
from app.services.shift_service import (
    auto_publish_enabled,
    get_shifts_in_range,
    validate_shift,
)
from app.services.text_utils import same_name

WEEKDAY_NAMES = [
    "Pondelok",
    "Utorok",
    "Streda",
    "Štvrtok",
    "Piatok",
    "Sobota",
    "Nedeľa",
]


def parse_week_start(value):
    """Z textu ``YYYY-MM-DD`` (ľubovoľný deň v týždni) vráti pondelok
    toho týždňa ako ``date``; pri neplatnom vstupe ``None``."""

    try:
        parsed = datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

    return parsed - timedelta(days=parsed.weekday())


def week_label(monday):
    sunday = monday + timedelta(days=6)

    return f"{monday.strftime('%d.%m.%Y')} – {sunday.strftime('%d.%m.%Y')}"


@dataclass
class PlanEntry:
    weekday: int
    employee_id: int
    employee_name: str
    start_time: str
    end_time: str
    shift_type: str
    department_id: int | None
    department_name: str | None


@dataclass
class PlanRow:
    entry: PlanEntry
    shift_date: str
    status: str  # "create" alebo "skip"
    reasons: list = field(default_factory=list)

    @property
    def weekday_name(self):
        return WEEKDAY_NAMES[self.entry.weekday]


def entries_from_week(monday, branch_id):
    """Smeny zamestnancov danej pobočky v týždni začínajúcom
    ``monday`` ako ``PlanEntry``."""

    sunday = monday + timedelta(days=6)

    shifts = get_shifts_in_range(
        monday.isoformat(), sunday.isoformat(), [branch_id]
    )

    entries = []

    for shift in shifts:
        shift_date = datetime.strptime(shift[3], "%Y-%m-%d").date()

        entries.append(
            PlanEntry(
                weekday=shift_date.weekday(),
                employee_id=shift[7],
                employee_name=f"{shift[1]} {shift[2]}",
                start_time=shift[4],
                end_time=shift[5],
                shift_type=shift[6],
                department_id=shift[8],
                department_name=shift[9],
            )
        )

    return entries


def entries_from_template(template_id):
    rows = db.session.execute(
        select(
            ShiftTemplateEntry,
            Employee.first_name,
            Employee.last_name,
            Department.name,
        )
        .join(Employee, ShiftTemplateEntry.employee_id == Employee.id)
        .outerjoin(
            Department, ShiftTemplateEntry.department_id == Department.id
        )
        .where(ShiftTemplateEntry.template_id == template_id)
    ).all()

    return [
        PlanEntry(
            weekday=entry.weekday,
            employee_id=entry.employee_id,
            employee_name=f"{first_name} {last_name}",
            start_time=entry.start_time,
            end_time=entry.end_time,
            shift_type=entry.shift_type,
            department_id=entry.department_id,
            department_name=department_name,
        )
        for entry, first_name, last_name, department_name in rows
    ]


def _identical_shift_exists(entry, shift_date):
    return (
        db.session.execute(
            select(Shift.id).where(
                Shift.employee_id == entry.employee_id,
                Shift.shift_date == shift_date,
                Shift.start_time == entry.start_time,
                Shift.end_time == entry.end_time,
            )
        ).first()
        is not None
    )


def run_plan(entries, target_monday, commit):
    """Vytvorí smeny z ``entries`` v týždni ``target_monday``.

    Vráti zoznam ``PlanRow`` (každá smena: ``create`` alebo ``skip`` s
    dôvodmi). ``commit=False`` je len náhľad - nič sa nezapíše.
    """

    rows = []
    published = 1 if auto_publish_enabled() else 0

    try:
        ordered = sorted(
            entries,
            key=lambda e: (e.weekday, e.start_time, e.employee_name),
        )

        for entry in ordered:
            shift_date = (
                target_monday + timedelta(days=entry.weekday)
            ).isoformat()

            row = PlanRow(entry=entry, shift_date=shift_date, status="skip")
            rows.append(row)

            employee = get_employee(entry.employee_id)

            if employee is None or not employee[5]:
                row.reasons.append("Zamestnanec je neaktívny.")
                continue

            if _identical_shift_exists(entry, shift_date):
                row.reasons.append("Rovnaká smena už v tomto týždni je.")
                continue

            errors = validate_shift(
                entry.employee_id,
                shift_date,
                entry.start_time,
                entry.end_time,
                department_id=entry.department_id,
            )

            if errors:
                row.reasons.extend(errors)
                continue

            db.session.add(
                Shift(
                    employee_id=entry.employee_id,
                    shift_date=shift_date,
                    start_time=entry.start_time,
                    end_time=entry.end_time,
                    shift_type=entry.shift_type,
                    department_id=entry.department_id,
                    created_at=datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    published=published,
                )
            )
            db.session.flush()

            row.status = "create"

        if commit:
            db.session.commit()
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        raise

    return rows


# ---------------------------------------------------------------------
# Šablóny
# ---------------------------------------------------------------------

def template_name_exists(name, branch_id):
    names = db.session.execute(
        select(ShiftTemplate.name).where(
            ShiftTemplate.branch_id == branch_id
        )
    ).scalars()

    return any(same_name(existing, name) for existing in names)


def save_template_from_week(name, branch_id, source_monday):
    """Uloží smeny pobočky z daného týždňa ako šablónu.

    Vráti ``(template_id, None)`` alebo ``(None, chybová správa)``.
    """

    name = (name or "").strip()

    if not name:
        return None, "Zadaj názov šablóny."

    if len(name) > 100:
        return None, "Názov šablóny môže mať najviac 100 znakov."

    if template_name_exists(name, branch_id):
        return None, "Šablóna s týmto názvom už v tejto pobočke existuje."

    entries = entries_from_week(source_monday, branch_id)

    if not entries:
        return None, "V tomto týždni nie sú v pobočke žiadne smeny."

    template = ShiftTemplate(
        name=name,
        branch_id=branch_id,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )

    for entry in entries:
        template.entries.append(
            ShiftTemplateEntry(
                weekday=entry.weekday,
                employee_id=entry.employee_id,
                start_time=entry.start_time,
                end_time=entry.end_time,
                shift_type=entry.shift_type,
                department_id=entry.department_id,
            )
        )

    db.session.add(template)
    db.session.commit()

    return template.id, None


def get_templates(branch_ids=None):
    """Šablóny ako (id, názov, branch_id, názov pobočky, počet smien)."""

    from sqlalchemy import func

    from app.orm_models import Branch

    query = (
        select(
            ShiftTemplate.id,
            ShiftTemplate.name,
            ShiftTemplate.branch_id,
            Branch.name,
            func.count(ShiftTemplateEntry.id),
        )
        .join(Branch, ShiftTemplate.branch_id == Branch.id)
        .outerjoin(
            ShiftTemplateEntry,
            ShiftTemplateEntry.template_id == ShiftTemplate.id,
        )
        .group_by(ShiftTemplate.id, ShiftTemplate.name, ShiftTemplate.branch_id, Branch.name)
        .order_by(Branch.name, ShiftTemplate.name)
    )

    if branch_ids is not None:
        query = query.where(ShiftTemplate.branch_id.in_(list(branch_ids)))

    return db.session.execute(query).all()


def get_template(template_id):
    """Jedna šablóna ako (id, názov, branch_id, názov pobočky) alebo
    ``None``."""

    from app.orm_models import Branch

    return db.session.execute(
        select(
            ShiftTemplate.id,
            ShiftTemplate.name,
            ShiftTemplate.branch_id,
            Branch.name,
        )
        .join(Branch, ShiftTemplate.branch_id == Branch.id)
        .where(ShiftTemplate.id == template_id)
    ).first()


def delete_template(template_id):
    template = db.session.get(ShiftTemplate, template_id)

    if template is None:
        return False

    db.session.delete(template)
    db.session.commit()

    return True
