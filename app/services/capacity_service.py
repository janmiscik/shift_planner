"""Kontrola minimálneho obsadenia smien (kapacita oddelenia).

Porovnáva počet naplánovaných zamestnancov v rámci jedného
(dátum, typ smeny, oddelenie) proti nastavenému minimu
(``departments.min_staff``). Upozorňuje len na kombinácie, kde už
existuje aspoň jedna naplánovaná smena - appka zatiaľ nemá koncept
"očakávaných" zmien bez toho, aby ich niekto vytvoril (to by riešili
šablóny/rotácie zmien, čo je samostatná funkcia).
"""

from collections import defaultdict

from sqlalchemy import select

from app.extensions import db
from app.orm_models import Department, Shift


def get_understaffed_shifts(start_date, end_date):
    """Nájde (dátum, typ smeny, oddelenie) kombinácie v danom rozsahu
    dátumov (vrátane), kde je naplánovaných menej ľudí, ako je
    nastavené minimum pre dané oddelenie.

    Vráti zoznam slovníkov zoradený podľa dátumu a typu smeny.
    """

    departments_with_min = db.session.execute(
        select(
            Department.id, Department.name, Department.min_staff
        ).where(
            Department.min_staff.isnot(None),
            Department.min_staff > 0,
        )
    ).all()

    if not departments_with_min:
        return []

    min_staff_by_department = {
        department_id: (name, min_staff)
        for department_id, name, min_staff in departments_with_min
    }

    query = select(
        Shift.department_id,
        Shift.shift_date,
        Shift.shift_type,
        Shift.employee_id,
    ).where(
        Shift.shift_date.between(start_date, end_date),
        Shift.department_id.in_(min_staff_by_department.keys()),
    )

    rows = db.session.execute(query).all()

    grouped = defaultdict(set)

    for department_id, shift_date, shift_type, employee_id in rows:
        key = (department_id, shift_date, shift_type)
        grouped[key].add(employee_id)

    understaffed = []

    for (department_id, shift_date, shift_type), employee_ids in grouped.items():
        department_name, min_staff = min_staff_by_department[department_id]
        scheduled = len(employee_ids)

        if scheduled < min_staff:
            understaffed.append(
                {
                    "department_id": department_id,
                    "department_name": department_name,
                    "shift_date": shift_date,
                    "shift_type": shift_type,
                    "scheduled": scheduled,
                    "min_staff": min_staff,
                }
            )

    understaffed.sort(
        key=lambda item: (item["shift_date"], item["shift_type"])
    )

    return understaffed
