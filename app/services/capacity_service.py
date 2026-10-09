"""Kontrola minimálneho obsadenia smien (kapacita oddelenia).

Porovnáva počet naplánovaných zamestnancov v rámci jedného
(dátum, typ smeny, oddelenie) proti nastavenému minimu
(``departments.min_staff``). Upozorňuje len na kombinácie, kde už
existuje aspoň jedna naplánovaná smena - appka zatiaľ nemá koncept
"očakávaných" zmien bez toho, aby ich niekto vytvoril (to by riešili
šablóny/rotácie zmien, čo je samostatná funkcia).

Smeny, ktoré nemajú priamo nastavené oddelenie (pole je nepovinné),
sa priradia k oddeleniu podľa zamestnanca - ak je zamestnanec
priradený práve k JEDNÉMU oddeleniu s nastaveným minimom. Ak je
priradený k viacerým takýmto oddeleniam, nedá sa jednoznačne určiť,
kam smena patrí, a takáto smena sa do kontroly nezapočíta (radšej
než hádať a zavádzať).
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

    from app.services.employee_department_service import (
        get_employee_department_ids,
    )

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
    tracked_department_ids = set(min_staff_by_department)

    query = select(
        Shift.department_id,
        Shift.shift_date,
        Shift.shift_type,
        Shift.employee_id,
    ).where(
        Shift.shift_date.between(start_date, end_date),
    )

    rows = db.session.execute(query).all()

    grouped = defaultdict(set)
    employee_tracked_cache = {}

    for department_id, shift_date, shift_type, employee_id in rows:
        if department_id is not None:
            if department_id not in tracked_department_ids:
                # Smena je explicitne v inom (nesledovanom) oddelení.
                continue

            effective_department_id = department_id
        else:
            # Smena nemá oddelenie - určíme ho podľa zamestnanca.
            if employee_id not in employee_tracked_cache:
                employee_tracked_cache[employee_id] = (
                    get_employee_department_ids(employee_id)
                    & tracked_department_ids
                )

            candidates = employee_tracked_cache[employee_id]

            if len(candidates) != 1:
                # Žiadne alebo viac možných oddelení - nejednoznačné.
                continue

            effective_department_id = next(iter(candidates))

        key = (effective_department_id, shift_date, shift_type)
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
