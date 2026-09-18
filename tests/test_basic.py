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
def client(tmp_path, monkeypatch):
    """Flask test client s dočasnou, prázdnou databázou pre každý test."""

    db_path = tmp_path / "test_shift_planner.db"

    import app.data.database as database_module

    monkeypatch.setattr(database_module, "DATABASE_PATH", db_path)

    from app.web.app import app as flask_app

    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    database_module.create_tables()

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
