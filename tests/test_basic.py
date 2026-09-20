"""Integračné testy pre Shift Planner (Flask test client + dočasná DB).

Každý test dostane čistú, izolovanú SQLite databázu (viď fixtúru
``client`` nižšie), takže sa testy navzájom neovplyvňujú a nezasahujú
do reálnej `app/data/shift_planner.db`.
"""

import re

import pytest


def _extract_csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "CSRF token sa v stránke nenašiel"
    return match.group(1)


@pytest.fixture
def client(tmp_path):
    """Flask test client s dočasnou, prázdnou databázou pre každý test.

    Appka sa vytvára nanovo pre KAŽDÝ test cez ``create_app()``
    (application factory) s vlastnou dočasnou cestou k SQLite súboru
    - vďaka tomu má každý test naozaj izolovanú databázu aj so
    SQLAlchemy (ktoré si engine viaže na konkrétnu appku pri jej
    vytvorení).
    """

    db_path = tmp_path / "test_shift_planner.db"

    from app.extensions import db
    from app.web.app import create_app

    flask_app = create_app(database_path=db_path)
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    with flask_app.app_context():
        db.create_all()

    with flask_app.test_client() as test_client:
        yield test_client


def get(client, url):
    return client.get(url)


def post(client, url, data=None):
    """POST s automaticky doplneným CSRF tokenom (GET tej istej URL
    najprv vytiahne token zo skrytého poľa formulára)."""

    response = client.get(url)
    token = _extract_csrf_token(response.get_data(as_text=True))

    payload = {"csrf_token": token}
    payload.update(data or {})

    return client.post(url, data=payload, follow_redirects=True)


def _csrf_token_for(client, page_url):
    response = client.get(page_url)
    return _extract_csrf_token(response.get_data(as_text=True))


# ---------------------------------------------------------------------
# Základná dostupnosť aplikácie
# ---------------------------------------------------------------------

def test_project_starts(client):
    response = get(client, "/")
    assert response.status_code == 200


def test_all_main_pages_load(client):
    for url in ("/", "/employees", "/departments", "/shifts", "/calendar"):
        response = get(client, url)
        assert response.status_code == 200, url


# ---------------------------------------------------------------------
# Zamestnanci
# ---------------------------------------------------------------------

def test_add_employee(client):
    response = post(
        client,
        "/employees/add",
        data={
            "first_name": "Jana",
            "last_name": "Nová",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )

    assert response.status_code == 200
    assert "Jana" in response.get_data(as_text=True)


def test_add_employee_requires_fields(client):
    response = post(
        client,
        "/employees/add",
        data={"first_name": "", "last_name": "", "position": "", "weekly_hours": ""},
    )

    body = response.get_data(as_text=True)
    assert "Zadaj meno." in body


# ---------------------------------------------------------------------
# Oddelenia
# ---------------------------------------------------------------------

def test_add_department(client):
    response = post(client, "/departments/add", data={"name": "Výroba"})

    assert response.status_code == 200
    assert "Výroba" in response.get_data(as_text=True)


def test_duplicate_department_name_is_rejected(client):
    post(client, "/departments/add", data={"name": "Výroba"})
    response = post(client, "/departments/add", data={"name": "Výroba"})

    assert "už existuje" in response.get_data(as_text=True)


# ---------------------------------------------------------------------
# Smeny - CRUD, kolízie, fond hodín, priradenie k oddeleniu
# ---------------------------------------------------------------------

def _create_employee_with_department(client, weekly_hours="20", dept_hours="10"):
    from app.services.employee_service import get_employees
    from app.services.department_service import get_departments

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Jana",
            "last_name": "Nová",
            "position": "Operátor",
            "weekly_hours": weekly_hours,
        },
    )
    post(client, "/departments/add", data={"name": "Výroba"})

    employee_id = get_employees()[0][0]
    department_id = get_departments()[0][0]

    post(
        client,
        f"/employees/{employee_id}/departments/add",
        data={"department_id": str(department_id), "weekly_hours": dept_hours},
    )

    return employee_id, department_id


def test_add_shift(client):
    employee_id, department_id = _create_employee_with_department(client)

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    shifts = get_shifts()
    assert len(shifts) == 1
    assert "field-error" not in response.get_data(as_text=True)


def test_shift_collision_is_rejected(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "12:00",
            "end_time": "16:00",
            "shift_type": "Poobedná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 1
    assert "už má v tomto čase inú smenu" in response.get_data(as_text=True)


def test_shift_exceeding_weekly_hours_is_rejected(client):
    employee_id, department_id = _create_employee_with_department(
        client, weekly_hours="20", dept_hours="10"
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",  # 6h
            "shift_type": "Ranná",
        },
    )

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-22",
            "start_time": "08:00",
            "end_time": "23:00",  # 15h -> 21h spolu, nad fondom 20h
            "shift_type": "Nočná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 1
    assert "prekračuje týždenný pracovný fond" in response.get_data(as_text=True)


def test_shift_rejected_for_unassigned_department(client):
    from app.services.employee_service import get_employees
    from app.services.department_service import get_departments

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Jana",
            "last_name": "Nová",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    post(client, "/departments/add", data={"name": "Výroba"})

    employee_id = get_employees()[0][0]
    department_id = get_departments()[0][0]

    # Zámerne NEpriradíme zamestnanca k oddeleniu.
    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 0
    assert "nie je priradený k vybranému oddeleniu" in response.get_data(as_text=True)


# ---------------------------------------------------------------------
# Nočné smeny (cez polnoc)
# ---------------------------------------------------------------------

def test_overnight_shift_is_accepted_and_duration_correct(client):
    from app.services.shift_service import shift_duration_hours

    assert shift_duration_hours("22:00", "06:00") == 8.0
    assert shift_duration_hours("23:30", "00:30") == 1.0

    employee_id, department_id = _create_employee_with_department(
        client, weekly_hours="40", dept_hours="40"
    )

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "22:00",
            "end_time": "06:00",
            "shift_type": "Nočná",
        },
    )

    from app.services.shift_service import get_shifts

    shifts = get_shifts()
    assert len(shifts) == 1, response.get_data(as_text=True)


def test_overnight_shift_collision_detected_next_morning(client):
    employee_id, department_id = _create_employee_with_department(
        client, weekly_hours="40", dept_hours="40"
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "22:00",
            "end_time": "06:00",
            "shift_type": "Nočná",
        },
    )

    # Táto smena na 22.9. o 05:00-09:00 sa prekrýva s koncom nočnej
    # smeny, ktorá začala 21.9. a končí až 22.9. o 06:00.
    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-22",
            "start_time": "05:00",
            "end_time": "09:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 1
    assert "už má v tomto čase inú smenu" in response.get_data(as_text=True)


def test_shift_with_equal_start_and_end_is_rejected(client):
    employee_id, department_id = _create_employee_with_department(client)

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "08:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 0
    assert "sa nemôžu zhodovať" in response.get_data(as_text=True)


def test_api_shifts_event_end_date_for_overnight_shift(client):
    employee_id, department_id = _create_employee_with_department(
        client, weekly_hours="40", dept_hours="40"
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "22:00",
            "end_time": "06:00",
            "shift_type": "Nočná",
        },
    )

    response = client.get("/api/shifts?start=2026-09-01&end=2026-09-30")
    events = response.get_json()

    assert len(events) == 1
    assert events[0]["start"] == "2026-09-21T22:00"
    assert events[0]["end"] == "2026-09-22T06:00"


# ---------------------------------------------------------------------
# Vizuálne upozornenie na limit hodín v kalendári
# ---------------------------------------------------------------------

def test_calendar_event_flags_near_weekly_limit(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Blízko",
            "position": "Operátor",
            "weekly_hours": "20",
        },
    )
    employee_id = get_employees()[0][0]

    # 8h + 10h = 18h z 20h fondu = 90 % -> "near"
    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )
    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-22",
            "start_time": "06:00",
            "end_time": "16:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/api/shifts?start=2026-09-01&end=2026-09-30")
    events = response.get_json()

    assert len(events) == 2
    for event in events:
        assert "hours-near" in event["className"]
        assert event["extendedProps"]["weeklyHours"]["status"] == "near"
        assert event["extendedProps"]["weeklyHours"]["used"] == 18.0


def test_calendar_event_flags_over_weekly_limit_after_fund_reduction(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Prekroceny",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "16:00",
            "shift_type": "Ranná",
        },
    )

    # Vedúci dodatočne zníži fond pod už naplánovaný počet hodín.
    post(
        client,
        f"/employees/edit/{employee_id}",
        data={
            "first_name": "Ján",
            "last_name": "Prekroceny",
            "position": "Operátor",
            "weekly_hours": "8",
        },
    )

    response = client.get("/api/shifts?start=2026-09-01&end=2026-09-30")
    events = response.get_json()

    assert len(events) == 1
    assert "hours-over" in events[0]["className"]
    assert events[0]["extendedProps"]["weeklyHours"]["status"] == "over"


def test_api_shifts_filtered_by_department(client):
    from app.services.employee_service import get_employees
    from app.services.department_service import get_departments

    post(client, "/departments/add", data={"name": "Výroba"})
    post(client, "/departments/add", data={"name": "Sklad"})
    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Novák",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    post(
        client,
        "/employees/add",
        data={
            "first_name": "Eva",
            "last_name": "Krátka",
            "position": "Skladníčka",
            "weekly_hours": "40",
        },
    )

    departments = get_departments()
    vyroba_id = [d[0] for d in departments if d[1] == "Výroba"][0]
    sklad_id = [d[0] for d in departments if d[1] == "Sklad"][0]

    employees = get_employees()
    jan_id = [e[0] for e in employees if e[1] == "Ján"][0]
    eva_id = [e[0] for e in employees if e[1] == "Eva"][0]

    post(
        client,
        f"/employees/{jan_id}/departments/add",
        data={"department_id": str(vyroba_id), "weekly_hours": "40"},
    )
    post(
        client,
        f"/employees/{eva_id}/departments/add",
        data={"department_id": str(sklad_id), "weekly_hours": "40"},
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(jan_id),
            "department_id": str(vyroba_id),
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )
    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(eva_id),
            "department_id": str(sklad_id),
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get(
        f"/api/shifts?start=2026-09-01&end=2026-09-30&department_id={vyroba_id}"
    )
    events = response.get_json()

    assert len(events) == 1
    assert events[0]["extendedProps"]["departmentName"] == "Výroba"


def test_shifts_export_ics(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/shifts/export.ics")
    content = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/calendar"
    assert content.startswith("BEGIN:VCALENDAR\r\n")
    assert content.strip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT" in content
    assert "DTSTART:20260921T060000" in content
    assert "DTEND:20260921T140000" in content


def test_shifts_export_ics_overnight_shift_spans_next_day(client):
    employee_id, department_id = _create_employee_with_department(
        client, weekly_hours="40", dept_hours="40"
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "22:00",
            "end_time": "06:00",
            "shift_type": "Nočná",
        },
    )

    response = client.get("/shifts/export.ics")
    content = response.get_data(as_text=True)

    assert "DTSTART:20260921T220000" in content
    assert "DTEND:20260922T060000" in content


def test_employee_export_ics_contains_only_their_shifts(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Novák",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    post(
        client,
        "/employees/add",
        data={
            "first_name": "Eva",
            "last_name": "Krátka",
            "position": "Skladníčka",
            "weekly_hours": "40",
        },
    )

    employees = get_employees()
    jan_id = [e[0] for e in employees if e[1] == "Ján"][0]
    eva_id = [e[0] for e in employees if e[1] == "Eva"][0]

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(jan_id),
            "department_id": "",
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )
    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(eva_id),
            "department_id": "",
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get(f"/employees/{jan_id}/export.ics")
    content = response.get_data(as_text=True)

    assert response.status_code == 200
    assert content.count("BEGIN:VEVENT") == 1
    assert "Ján Novák" in content
    assert "Eva Krátka" not in content


def test_employee_export_ics_404_for_missing_employee(client):
    response = client.get("/employees/9999/export.ics")

    assert response.status_code == 404


def test_calendar_export_ics(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/calendar/export.ics?year=2026&month=9")

    assert response.status_code == 200
    assert response.mimetype == "text/calendar"
    assert "BEGIN:VEVENT" in response.get_data(as_text=True)


def _setup_department_filter_scenario(client):
    """Ján je priradený len k Výrobe, Eva k Výrobe aj Skladu, Peter
    nikam. Všetky ich smeny sú pridané BEZ explicitného oddelenia
    (simulácia starších dát pred zavedením poľa oddelenia)."""

    from app.services.employee_service import get_employees
    from app.services.department_service import get_departments

    post(client, "/departments/add", data={"name": "Výroba"})
    post(client, "/departments/add", data={"name": "Sklad"})

    departments = get_departments()
    vyroba_id = [d[0] for d in departments if d[1] == "Výroba"][0]
    sklad_id = [d[0] for d in departments if d[1] == "Sklad"][0]

    for first_name in ("Ján", "Eva", "Peter"):
        post(
            client,
            "/employees/add",
            data={
                "first_name": first_name,
                "last_name": "Test",
                "position": "Operátor",
                "weekly_hours": "40",
            },
        )

    employees = get_employees()
    jan_id = [e[0] for e in employees if e[1] == "Ján"][0]
    eva_id = [e[0] for e in employees if e[1] == "Eva"][0]
    peter_id = [e[0] for e in employees if e[1] == "Peter"][0]

    post(
        client,
        f"/employees/{jan_id}/departments/add",
        data={"department_id": str(vyroba_id), "weekly_hours": "40"},
    )
    post(
        client,
        f"/employees/{eva_id}/departments/add",
        data={"department_id": str(vyroba_id), "weekly_hours": "20"},
    )
    post(
        client,
        f"/employees/{eva_id}/departments/add",
        data={"department_id": str(sklad_id), "weekly_hours": "20"},
    )

    for employee_id in (jan_id, eva_id, peter_id):
        post(
            client,
            "/shifts/add",
            data={
                "employee_id": str(employee_id),
                "department_id": "",
                "shift_date": "2026-09-21",
                "start_time": "06:00",
                "end_time": "14:00",
                "shift_type": "Ranná",
            },
        )

    return {
        "vyroba_id": vyroba_id,
        "sklad_id": sklad_id,
        "jan_id": jan_id,
        "eva_id": eva_id,
        "peter_id": peter_id,
    }


def test_department_filter_falls_back_to_employee_assignment(client):
    ids = _setup_department_filter_scenario(client)

    response = client.get(
        "/api/shifts?start=2026-09-01&end=2026-09-30"
        f"&department_id={ids['vyroba_id']}"
    )
    names = sorted(
        e["extendedProps"]["employeeName"] for e in response.get_json()
    )

    # Ján aj Eva majú priradenie do Výroby (aj keď ich smeny samotné
    # oddelenie explicitne nemajú nastavené) - Peter nikam priradený
    # nie je, takže sa neukáže.
    assert names == ["Eva Test", "Ján Test"]

    response = client.get(
        "/api/shifts?start=2026-09-01&end=2026-09-30"
        f"&department_id={ids['sklad_id']}"
    )
    names = sorted(
        e["extendedProps"]["employeeName"] for e in response.get_json()
    )

    assert names == ["Eva Test"]


def test_backfill_shift_departments_only_unambiguous_cases(client):
    from app.services.shift_service import (
        backfill_shift_departments,
        get_shifts,
    )

    _setup_department_filter_scenario(client)

    updated, skipped = backfill_shift_departments()

    assert updated == 1
    assert skipped == 2

    shifts = get_shifts()
    jan_shift = [s for s in shifts if s[1] == "Ján"][0]
    eva_shift = [s for s in shifts if s[1] == "Eva"][0]
    peter_shift = [s for s in shifts if s[1] == "Peter"][0]

    assert jan_shift[9] == "Výroba"
    assert eva_shift[8] is None
    assert peter_shift[8] is None


# ---------------------------------------------------------------------
# Kapacita oddelenia (minimálny počet ľudí na zmene)
# ---------------------------------------------------------------------

def test_understaffed_shift_is_detected(client):
    from app.services.employee_service import get_employees
    from app.services.capacity_service import get_understaffed_shifts

    post(client, "/departments/add", data={"name": "Výroba", "min_staff": "3"})

    from app.services.department_service import get_departments

    dep_id = get_departments()[0][0]

    for first_name in ("Ján", "Eva"):
        post(
            client,
            "/employees/add",
            data={
                "first_name": first_name,
                "last_name": "Test",
                "position": "Operátor",
                "weekly_hours": "40",
            },
        )

    for employee in get_employees():
        post(
            client,
            f"/employees/{employee[0]}/departments/add",
            data={"department_id": str(dep_id), "weekly_hours": "40"},
        )
        post(
            client,
            "/shifts/add",
            data={
                "employee_id": str(employee[0]),
                "department_id": str(dep_id),
                "shift_date": "2026-09-21",
                "start_time": "06:00",
                "end_time": "14:00",
                "shift_type": "Ranná",
            },
        )

    result = get_understaffed_shifts("2026-09-01", "2026-09-30")

    assert len(result) == 1
    assert result[0]["scheduled"] == 2
    assert result[0]["min_staff"] == 3
    assert result[0]["department_name"] == "Výroba"


def test_calendar_page_shows_capacity_warning(client):
    from app.services.employee_service import get_employees
    from app.services.department_service import get_departments

    post(client, "/departments/add", data={"name": "Výroba", "min_staff": "3"})
    dep_id = get_departments()[0][0]

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Test",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        f"/employees/{employee_id}/departments/add",
        data={"department_id": str(dep_id), "weekly_hours": "40"},
    )
    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(dep_id),
            "shift_date": "2026-09-21",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/calendar?year=2026&month=9")
    body = response.get_data(as_text=True)

    assert "Nedostatočné obsadenie" in body
    assert "naplánovaných 1" in body


# ---------------------------------------------------------------------
# Absencie (dovolenka, PN, OČR, náhradné voľno)
# ---------------------------------------------------------------------

def test_add_absence(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    response = post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "Dovolenka",
            "start_date": "2026-09-20",
            "end_date": "2026-09-25",
            "note": "Letná dovolenka",
        },
    )

    from app.services.absence_service import get_absences

    absences = get_absences()
    assert response.status_code == 200
    assert len(absences) == 1
    assert absences[0][4] == "Dovolenka"


def test_shift_blocked_during_absence(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "Dovolenka",
            "start_date": "2026-09-20",
            "end_date": "2026-09-25",
        },
    )

    response = post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-22",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 0
    assert "schválenú neprítomnosť" in response.get_data(as_text=True)


def test_shift_allowed_outside_absence_range(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "Dovolenka",
            "start_date": "2026-09-20",
            "end_date": "2026-09-25",
        },
    )

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-26",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    assert len(get_shifts()) == 1


def test_adding_absence_warns_about_existing_shifts(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": "",
            "shift_date": "2026-09-27",
            "start_time": "06:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "PN",
            "start_date": "2026-09-27",
            "end_date": "2026-09-28",
        },
    )

    assert "už naplánovaných" in response.get_data(as_text=True)


def test_absence_end_before_start_is_rejected(client):
    from app.services.employee_service import get_employees

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    response = post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "Dovolenka",
            "start_date": "2026-09-25",
            "end_date": "2026-09-20",
        },
    )

    from app.services.absence_service import get_absences

    assert len(get_absences()) == 0
    assert "skorší ako dátum začiatku" in response.get_data(as_text=True)


def test_delete_absence(client):
    from app.services.employee_service import get_employees
    from app.services.absence_service import get_absences

    post(
        client,
        "/employees/add",
        data={
            "first_name": "Ján",
            "last_name": "Dovolenkár",
            "position": "Operátor",
            "weekly_hours": "40",
        },
    )
    employee_id = get_employees()[0][0]

    post(
        client,
        "/absences/add",
        data={
            "employee_id": str(employee_id),
            "absence_type": "Dovolenka",
            "start_date": "2026-09-20",
            "end_date": "2026-09-25",
        },
    )

    absence_id = get_absences()[0][0]
    token = _csrf_token_for(client, "/absences")

    client.post(
        f"/absences/delete/{absence_id}",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert len(get_absences()) == 0


# ---------------------------------------------------------------------
# Zálohovanie databázy
# ---------------------------------------------------------------------

def test_backup_creates_file_next_to_database(tmp_path):
    from app.services.backup_service import create_backup, list_backups

    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"fake sqlite content")

    backup_path = create_backup(db_path, label="test")

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"
    assert backup_path.read_bytes() == b"fake sqlite content"

    backups = list_backups(db_path)
    assert len(backups) == 1
    assert backups[0][0] == backup_path


def test_backup_returns_none_when_no_database(tmp_path):
    from app.services.backup_service import create_backup

    db_path = tmp_path / "does_not_exist.db"

    assert create_backup(db_path) is None


def test_restore_backup_brings_back_old_content(tmp_path):
    from app.services.backup_service import create_backup, restore_backup

    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"original content")

    backup_path = create_backup(db_path, label="snapshot")

    # Databáza sa medzitým "pokazí"/zmení.
    db_path.write_bytes(b"corrupted content")

    restore_backup(backup_path.name, db_path)

    assert db_path.read_bytes() == b"original content"


def test_restore_missing_backup_raises(tmp_path):
    from app.services.backup_service import restore_backup

    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"content")

    with pytest.raises(FileNotFoundError):
        restore_backup("neexistujuca_zaloha.db", db_path)


def test_admin_backup_route_downloads_database(client):
    response = client.get("/admin/backup")

    assert response.status_code == 200
    assert response.mimetype == "application/x-sqlite3"
    assert response.data[:16] == b"SQLite format 3\x00"


def test_shift_delete(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    shift_id = get_shifts()[0][0]

    token = _csrf_token_for(client, "/shifts")
    client.post(
        f"/shifts/delete/{shift_id}",
        data={"csrf_token": token},
        follow_redirects=True,
    )

    assert len(get_shifts()) == 0


# ---------------------------------------------------------------------
# JSON API (kalendár, drag-and-drop presun, export)
# ---------------------------------------------------------------------

def test_api_employee_departments(client):
    employee_id, department_id = _create_employee_with_department(client)

    response = client.get(f"/api/employees/{employee_id}/departments")

    assert response.status_code == 200
    assert response.get_json() == [{"id": department_id, "name": "Výroba"}]


def test_api_shifts_events_for_fullcalendar(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/api/shifts?year=2026&month=9")
    events = response.get_json()

    assert len(events) == 1
    assert events[0]["start"] == "2026-09-21T08:00"


def test_api_shifts_events_with_date_range_spanning_month_boundary(client):
    """Regresný test: FullCalendar posiela pre mesačnú mriežku začiatok
    a koniec zarovnané na celé týždne, čo môže siahať do susedných
    mesiacov. API musí správne vrátiť smeny podľa reálneho rozsahu
    (start/end), nielen podľa year/month prvého dňa mriežky."""

    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-01",  # utorok - mriežka začína pondelkom 31.8.
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/api/shifts?start=2026-08-31&end=2026-10-12")
    events = response.get_json()

    assert len(events) == 1
    assert events[0]["start"] == "2026-09-01T08:00"


def test_api_move_shift_respects_validation(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    from app.services.shift_service import get_shifts

    shift_id = get_shifts()[0][0]
    token = _csrf_token_for(client, "/departments")

    response = client.post(
        f"/api/shifts/{shift_id}/move",
        json={
            "shift_date": "2026-09-21",
            "start_time": "09:00",
            "end_time": "15:00",
        },
        headers={"X-CSRFToken": token},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_shifts_export_xlsx(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/shifts/export.xlsx")

    assert response.status_code == 200
    assert response.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(response.data) > 0


def test_shifts_export_pdf(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/shifts/export.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data[:4] == b"%PDF"


def test_calendar_export_pdf(client):
    employee_id, department_id = _create_employee_with_department(client)

    post(
        client,
        "/shifts/add",
        data={
            "employee_id": str(employee_id),
            "department_id": str(department_id),
            "shift_date": "2026-09-21",
            "start_time": "08:00",
            "end_time": "14:00",
            "shift_type": "Ranná",
        },
    )

    response = client.get("/calendar/export.pdf?year=2026&month=9")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data[:4] == b"%PDF"


# ---------------------------------------------------------------------
# CSRF ochrana
# ---------------------------------------------------------------------

def test_post_without_csrf_token_is_rejected(client):
    response = client.post("/departments/add", data={"name": "Bez tokenu"})

    assert response.status_code == 400
