"""Testy audit logu (kto, kedy a čo zmenil)."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest


def _csrf(html):
    # Poradie atribútov v <input> sa medzi šablónami líši.
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa v stránke nenašiel")


@pytest.fixture
def aw(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "audit.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    ids = {}

    with app.app_context():
        db.create_all()

        from app.services.auth_service import (
            create_admin,
            create_employee_user,
            create_manager,
        )
        from app.services.branch_service import add_branch
        from app.services.department_service import add_department
        from app.services.employee_department_service import (
            add_employee_department,
        )
        from app.services.employee_service import add_employee

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")
        ids["dep_a"] = add_department("Výroba", branch_id=ids["a"])
        ids["anna"] = add_employee("Anna", "Prvá", "Op", 40, ids["a"])
        ids["boris"] = add_employee("Boris", "Druhý", "Op", 40, ids["b"])
        add_employee_department(ids["anna"], ids["dep_a"], 40)

        create_admin("admin", "adminpass1")
        create_manager("mgr_a", "managerpass1", [ids["a"]])
        create_manager("mgr_ab", "managerpass1", [ids["a"], ids["b"]])
        create_employee_user(ids["anna"], "emp_a", "emppass12")

        # Príprava dát (bez používateľa = "systém") nás nezaujíma.
        db.session.query(__import__("app.orm_models", fromlist=["AuditLog"]).AuditLog).delete()
        db.session.commit()

    app.test_ids = ids

    return app


def login(app, username, password):
    client = app.test_client()
    token = _csrf(client.get("/login").get_data(as_text=True))
    client.post(
        "/login",
        data={"csrf_token": token, "username": username, "password": password},
    )
    return client


def post(client, page, url, data=None):
    token = _csrf(client.get(page).get_data(as_text=True))

    return client.post(url, data={"csrf_token": token, **(data or {})})


def entries(app, **filters):
    from app.extensions import db
    from app.orm_models import AuditLog

    with app.app_context():
        query = db.session.query(AuditLog).order_by(AuditLog.id)

        for key, value in filters.items():
            query = query.filter(getattr(AuditLog, key) == value)

        return [
            {
                "id": e.id, "username": e.username, "user_id": e.user_id,
                "action": e.action, "entity_type": e.entity_type,
                "entity_id": e.entity_id, "branch_id": e.branch_id,
                "summary": e.summary, "created_at": e.created_at,
            }
            for e in query.all()
        ]


def summaries(app, **filters):
    return [e["summary"] for e in entries(app, **filters)]


SHIFT = {
    "department_id": "",
    "shift_date": "2026-09-21",
    "start_time": "06:00",
    "end_time": "14:00",
    "shift_type": "Ranná",
}


def add_shift_via_ui(client, app, **overrides):
    data = {"employee_id": str(app.test_ids["anna"]), **SHIFT, **overrides}

    return post(client, "/shifts/add", "/shifts/add", data)


def shift_ids(app):
    from app.extensions import db
    from app.orm_models import Shift

    with app.app_context():
        return [s.id for s in db.session.query(Shift).order_by(Shift.id)]


# ---------------------------------------------------------------------
# Smeny
# ---------------------------------------------------------------------

def test_shift_create_update_delete_are_logged_with_the_actor(aw):
    manager = login(aw, "mgr_a", "managerpass1")
    ids = aw.test_ids

    assert add_shift_via_ui(manager, aw).status_code == 302
    shift_id = shift_ids(aw)[-1]

    created = entries(aw, action="shift.create")
    assert len(created) == 1

    row = created[0]
    assert row["username"] == "mgr_a" and row["user_id"] is not None
    assert row["branch_id"] == ids["a"]
    assert row["entity_id"] == shift_id
    assert "Anna Prvá" in row["summary"]
    assert "2026-09-21" in row["summary"] and "06:00–14:00" in row["summary"]

    # Úprava s rozdielom (a nezmenené polia sa nespomenú).
    post(
        manager, f"/shifts/edit/{shift_id}", f"/shifts/edit/{shift_id}",
        {"employee_id": str(ids["anna"]), **SHIFT, "start_time": "07:00"},
    )
    updated = entries(aw, action="shift.update")
    assert len(updated) == 1
    assert "začiatok: 06:00 → 07:00" in updated[0]["summary"]
    assert "koniec" not in updated[0]["summary"]
    assert updated[0]["entity_id"] == shift_id

    # Uloženie bez zmeny nevytvorí záznam.
    post(
        manager, f"/shifts/edit/{shift_id}", f"/shifts/edit/{shift_id}",
        {"employee_id": str(ids["anna"]), **SHIFT, "start_time": "07:00"},
    )
    assert len(entries(aw, action="shift.update")) == 1

    post(manager, "/shifts", f"/shifts/delete/{shift_id}")
    deleted = entries(aw, action="shift.delete")
    assert len(deleted) == 1
    assert "Zmazaná smena" in deleted[0]["summary"]
    assert deleted[0]["username"] == "mgr_a"


def test_moving_a_shift_in_the_calendar_is_logged(aw):
    manager = login(aw, "mgr_a", "managerpass1")

    add_shift_via_ui(manager, aw)
    shift_id = shift_ids(aw)[-1]

    token = _csrf(manager.get("/calendar").get_data(as_text=True))
    response = manager.post(
        f"/api/shifts/{shift_id}/move",
        json={"shift_date": "2026-09-23", "start_time": "06:00", "end_time": "14:00"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200

    moved = entries(aw, action="shift.update")
    assert len(moved) == 1
    assert "dátum: 2026-09-21 → 2026-09-23" in moved[0]["summary"]
    assert moved[0]["username"] == "mgr_a"


def test_publish_and_unpublish_are_single_summary_entries(aw):
    manager = login(aw, "mgr_a", "managerpass1")
    ids = aw.test_ids

    add_shift_via_ui(manager, aw)
    add_shift_via_ui(manager, aw, shift_date="2026-09-22")

    data = {"branch_id": str(ids["a"]), "week_date": "2026-09-21"}
    post(manager, "/shifts/publish", "/shifts/publish", {**data, "action": "publish"})

    published = entries(aw, action="shift.publish")
    assert len(published) == 1
    assert "2 smien" in published[0]["summary"] and "Košice" in published[0]["summary"]
    assert published[0]["entity_type"] == "week"
    assert published[0]["branch_id"] == ids["a"]
    assert published[0]["username"] == "mgr_a"

    # Hromadný UPDATE nevytvoril riadkové záznamy.
    assert entries(aw, action="shift.update") == []

    post(manager, "/shifts/publish", "/shifts/publish", {**data, "action": "unpublish"})
    assert len(entries(aw, action="shift.unpublish")) == 1

    # Keď niet čo zmeniť, záznam nevznikne.
    post(manager, "/shifts/publish", "/shifts/publish", {**data, "action": "unpublish"})
    assert len(entries(aw, action="shift.unpublish")) == 1


def test_copy_week_writes_one_summary_and_preview_writes_nothing(aw):
    manager = login(aw, "mgr_a", "managerpass1")
    ids = aw.test_ids

    add_shift_via_ui(manager, aw)
    add_shift_via_ui(manager, aw, shift_date="2026-09-22")
    before = len(entries(aw))

    data = {
        "branch_id": str(ids["a"]),
        "source_date": "2026-09-21",
        "target_date": "2026-09-28",
    }

    post(manager, "/shifts/copy-week", "/shifts/copy-week", {**data, "action": "preview"})
    assert len(entries(aw)) == before

    post(manager, "/shifts/copy-week", "/shifts/copy-week", {**data, "action": "copy"})

    new = entries(aw)[before:]
    assert [e["action"] for e in new] == ["shift.bulk_create"]
    assert "vytvorených 2" in new[0]["summary"] and "Košice" in new[0]["summary"]
    assert new[0]["username"] == "mgr_a" and new[0]["entity_type"] == "week"


def test_templates_are_logged(aw):
    manager = login(aw, "mgr_a", "managerpass1")
    ids = aw.test_ids

    add_shift_via_ui(manager, aw)

    post(
        manager, "/shifts/templates", "/shifts/templates/save",
        {"branch_id": str(ids["a"]), "source_date": "2026-09-21", "name": "Štandard"},
    )
    created = entries(aw, action="template.create")
    assert len(created) == 1 and "Štandard" in created[0]["summary"]
    assert created[0]["branch_id"] == ids["a"]

    from app.services.week_plan_service import get_templates

    with aw.app_context():
        template_id = get_templates()[0][0]

    post(
        manager, "/shifts/templates", f"/shifts/templates/{template_id}/apply",
        {"action": "apply", "target_date": "2026-10-05"},
    )
    assert len(entries(aw, action="shift.bulk_create")) == 1

    post(manager, "/shifts/templates", f"/shifts/templates/{template_id}/delete")
    assert len(entries(aw, action="template.delete")) == 1


# ---------------------------------------------------------------------
# Absencie a žiadosti o voľno
# ---------------------------------------------------------------------

def test_absence_changes_are_logged(aw):
    manager = login(aw, "mgr_a", "managerpass1")
    ids = aw.test_ids

    absence = {
        "employee_id": str(ids["anna"]),
        "absence_type": "Dovolenka",
        "start_date": "2026-10-05",
        "end_date": "2026-10-07",
        "note": "",
    }
    post(manager, "/absences/add", "/absences/add", absence)

    created = entries(aw, action="absence.create")
    assert len(created) == 1
    assert "Anna Prvá" in created[0]["summary"] and "Dovolenka" in created[0]["summary"]
    assert created[0]["branch_id"] == ids["a"]

    absence_id = created[0]["entity_id"]
    post(
        manager, f"/absences/edit/{absence_id}", f"/absences/edit/{absence_id}",
        {**absence, "absence_type": "PN"},
    )
    assert "druh: Dovolenka → PN" in entries(aw, action="absence.update")[0]["summary"]

    post(manager, "/absences", f"/absences/delete/{absence_id}")
    assert len(entries(aw, action="absence.delete")) == 1


def test_leave_request_flow_is_logged_per_actor(aw):
    employee = login(aw, "emp_a", "emppass12")
    start = (date.today() + timedelta(days=30)).isoformat()
    end = (date.today() + timedelta(days=32)).isoformat()

    post(
        employee, "/my-requests", "/my-requests",
        {"absence_type": "Dovolenka", "start_date": start, "end_date": end, "note": ""},
    )

    submitted = entries(aw, action="leave_request.create")
    assert len(submitted) == 1
    assert submitted[0]["username"] == "emp_a"
    assert "Podaná žiadosť o voľno" in submitted[0]["summary"]
    assert submitted[0]["branch_id"] == aw.test_ids["a"]

    manager = login(aw, "mgr_a", "managerpass1")
    request_id = submitted[0]["entity_id"]
    post(
        manager, "/leave-requests", f"/leave-requests/{request_id}/approve",
        {"decision_note": "OK"},
    )

    decided = entries(aw, action="leave_request.update")
    assert len(decided) == 1 and decided[0]["username"] == "mgr_a"
    assert "schválená" in decided[0]["summary"] and "OK" in decided[0]["summary"]

    created_absences = entries(aw, action="absence.create")
    assert len(created_absences) == 1 and created_absences[0]["username"] == "mgr_a"


# ---------------------------------------------------------------------
# Správa (admin): účty, pobočky, zamestnanci
# ---------------------------------------------------------------------

def test_admin_operations_are_logged(aw):
    admin = login(aw, "admin", "adminpass1")
    ids = aw.test_ids

    post(admin, "/branches/add", "/branches/add", {"name": "Nitra"})
    assert any("Pridaná pobočka: Nitra" in s for s in summaries(aw, entity_type="branch"))

    post(admin, "/branches", f"/branches/toggle/{ids['b']}")
    assert any("stav: aktívna → neaktívna" in s for s in summaries(aw, entity_type="branch"))

    post(admin, "/departments/add", "/departments/add", {"branch_id": str(ids["a"]), "name": "Sklad"})
    assert any("Pridané oddelenie: Sklad" in s for s in summaries(aw, entity_type="department"))

    post(
        admin, "/employees/add", "/employees/add",
        {
            "branch_id": str(ids["a"]), "first_name": "Cyril", "last_name": "Tretí",
            "position": "Skladník", "weekly_hours": "40",
            "username": "cyril", "password": "cyrilpass1",
        },
    )
    assert any("Pridaný zamestnanec: Cyril Tretí" in s for s in summaries(aw, entity_type="employee"))
    assert any("Vytvorený účet: cyril (employee)" in s for s in summaries(aw, entity_type="user"))

    post(admin, "/employees", f"/employees/toggle/{ids['anna']}")
    assert any("stav: aktívny → neaktívny" in s for s in summaries(aw, entity_type="employee"))

    assert all(e["username"] == "admin" for e in entries(aw))


def test_user_and_role_changes_are_logged_without_secrets(aw):
    from app.services.auth_service import get_user_by_username

    admin = login(aw, "admin", "adminpass1")
    ids = aw.test_ids

    post(
        admin, "/users/add", "/users/add",
        {"username": "novy", "password": "novypass12", "role": "manager",
         "branch_ids": [str(ids["a"])]},
    )
    assert any("Vytvorený účet: novy (manager)" in s for s in summaries(aw, entity_type="user"))

    with aw.app_context():
        user_id = get_user_by_username("novy").id

    post(
        admin, f"/users/edit/{user_id}", f"/users/edit/{user_id}",
        {"username": "novy", "password": "ineheslo99", "branch_ids": [str(ids["b"])]},
    )
    # Úprava účtu je viac samostatných zápisov (heslo, pobočky), každý
    # má vlastný záznam.
    edit = " | ".join(
        s for s in summaries(aw, entity_type="user") if s.startswith("Upravený účet novy")
    )
    assert "heslo zmenené" in edit
    assert "odobraté Košice" in edit and "pridané Prešov" in edit

    # Funkcia manažéra pre zamestnanca (rola sa mení z employee na manager).
    post(
        admin, f"/employees/edit/{ids['anna']}", f"/employees/{ids['anna']}/manager",
        {"manager_enabled": "1", "branch_ids": [str(ids["a"])]},
    )
    assert any("rola: employee → manager" in s for s in summaries(aw, entity_type="user"))

    post(admin, "/users", f"/users/delete/{user_id}")
    assert any("Zmazaný účet: novy" in s for s in summaries(aw, entity_type="user"))

    # Žiadne heslá ani hashe v záznamoch.
    joined = " ".join(summaries(aw))
    for secret in ("novypass12", "ineheslo99", "adminpass1", "pbkdf2", "scrypt"):
        assert secret not in joined


def test_user_changing_own_password_is_logged_under_their_name(aw):
    employee = login(aw, "emp_a", "emppass12")

    post(
        employee, "/account/password", "/account/password",
        {"current_password": "emppass12", "new_password": "novyheslo1",
         "confirm_password": "novyheslo1"},
    )

    changed = [e for e in entries(aw, entity_type="user") if "heslo zmenené" in e["summary"]]
    assert len(changed) == 1 and changed[0]["username"] == "emp_a"


def test_deleted_user_keeps_name_in_old_entries(aw):
    from app.services.auth_service import get_user_by_username

    manager = login(aw, "mgr_ab", "managerpass1")
    add_shift_via_ui(manager, aw)

    admin = login(aw, "admin", "adminpass1")

    with aw.app_context():
        user_id = get_user_by_username("mgr_ab").id

    post(admin, "/users", f"/users/delete/{user_id}")

    row = entries(aw, action="shift.create")[0]
    assert row["username"] == "mgr_ab"
    assert row["user_id"] is None   # odkaz na zmazaný účet sa vynuluje


# ---------------------------------------------------------------------
# Mimo webu a odolnosť
# ---------------------------------------------------------------------

def test_changes_outside_the_web_are_logged_as_system(aw):
    from app.services.employee_service import add_employee

    with aw.app_context():
        add_employee("Dana", "Štvrtá", "Op", 40, aw.test_ids["a"])

    row = entries(aw, action="employee.create")[0]
    assert row["username"] is None and row["user_id"] is None
    assert "Dana Štvrtá" in row["summary"]


def test_a_failing_audit_never_breaks_the_operation(aw, monkeypatch, caplog):
    import logging

    from app.orm_models import Shift
    import app.services.audit_service as audit

    def broken(session, obj, op):
        raise RuntimeError("chyba v popise")

    monkeypatch.setitem(audit._TRACKED, Shift, ("shift", broken))

    manager = login(aw, "mgr_a", "managerpass1")

    with caplog.at_level(logging.ERROR):
        response = add_shift_via_ui(manager, aw)

    assert response.status_code == 302
    assert len(shift_ids(aw)) == 1                       # smena vznikla
    assert entries(aw, action="shift.create") == []      # záznam nie
    assert "nepodarilo sa popísať zmenu" in caplog.text


# Potrebuje dve nezávislé databázy; v režime TEST_DATABASE_URL by druhé
# create_app zmazalo spoločnú testovaciu databázu.
@pytest.mark.sqlite_only
def test_listeners_are_registered_only_once(aw, tmp_path):
    from app.web.app import create_app

    create_app(database_path=tmp_path / "second.db")   # druhá appka v procese

    manager = login(aw, "mgr_a", "managerpass1")
    add_shift_via_ui(manager, aw)

    assert len(entries(aw, action="shift.create")) == 1


# ---------------------------------------------------------------------
# Zobrazenie, práva, filtre, export
# ---------------------------------------------------------------------

def _seed_two_branches(aw):
    manager_ab = login(aw, "mgr_ab", "managerpass1")
    ids = aw.test_ids

    add_shift_via_ui(manager_ab, aw)                                     # Košice
    add_shift_via_ui(manager_ab, aw, employee_id=str(ids["boris"]),
                     shift_date="2026-09-22")                           # Prešov

    admin = login(aw, "admin", "adminpass1")
    post(admin, "/branches/add", "/branches/add", {"name": "Nitra"})


def test_managers_see_only_their_branches_and_no_admin_entries(aw):
    _seed_two_branches(aw)

    html = login(aw, "mgr_a", "managerpass1").get("/audit").get_data(as_text=True)
    assert "Anna Prvá" in html
    assert "Boris Druhý" not in html
    assert "Pridaná pobočka" not in html and "Vytvorený účet" not in html

    admin_html = login(aw, "admin", "adminpass1").get("/audit").get_data(as_text=True)
    assert "Anna Prvá" in admin_html and "Boris Druhý" in admin_html
    assert "Pridaná pobočka: Nitra" in admin_html


def test_manager_branch_filter_and_foreign_branch_is_ignored(aw):
    _seed_two_branches(aw)
    ids = aw.test_ids

    both = login(aw, "mgr_ab", "managerpass1")
    html = both.get(f"/audit?branch_id={ids['b']}").get_data(as_text=True)
    assert "Boris Druhý" in html and "Anna Prvá" not in html

    single = login(aw, "mgr_a", "managerpass1")
    html = single.get(f"/audit?branch_id={ids['b']}").get_data(as_text=True)
    assert "Boris Druhý" not in html and "Anna Prvá" in html


def test_employee_cannot_open_audit(aw):
    employee = login(aw, "emp_a", "emppass12")

    assert employee.get("/audit").status_code == 403
    assert employee.get("/audit/export.csv").status_code == 403


def test_filters_by_type_user_and_date(aw):
    _seed_two_branches(aw)

    admin = login(aw, "admin", "adminpass1")

    html = admin.get("/audit?entity=branch").get_data(as_text=True)
    assert "Pridaná pobočka: Nitra" in html and "Pridaná smena" not in html

    html = admin.get("/audit?user=mgr_ab").get_data(as_text=True)
    assert "Pridaná smena" in html and "Pridaná pobočka" not in html

    today = date.today().isoformat()
    html = admin.get(f"/audit?date_from={today}&date_to={today}").get_data(as_text=True)
    assert "Pridaná smena" in html

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    html = admin.get(f"/audit?date_from={tomorrow}").get_data(as_text=True)
    assert "Žiadne záznamy" in html

    # Neplatný filter sa ignoruje.
    assert admin.get("/audit?entity=nezmysel&date_from=zle").status_code == 200

    # Manažér nemôže filtrovať admin-ové typy.
    manager = login(aw, "mgr_a", "managerpass1")
    html = manager.get("/audit?entity=user").get_data(as_text=True)
    assert "Vytvorený účet" not in html


def test_pagination(aw):
    from app.services.employee_service import add_employee

    with aw.app_context():
        for number in range(60):
            add_employee(f"Meno{number}", "Priezvisko", "Op", 40, aw.test_ids["a"])

    admin = login(aw, "admin", "adminpass1")

    first = admin.get("/audit").get_data(as_text=True)
    assert "Strana 1 z 2" in first and "Staršie" in first and "Novšie" not in first

    second = admin.get("/audit?page=2").get_data(as_text=True)
    assert "Strana 2 z 2" in second and "Novšie" in second

    # Strana mimo rozsahu nespadne.
    assert admin.get("/audit?page=999").status_code == 200
    assert admin.get("/audit?page=-5").status_code == 200


def test_csv_export_respects_scope_and_filters(aw):
    _seed_two_branches(aw)

    admin = login(aw, "admin", "adminpass1")
    response = admin.get("/audit/export.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "zaznamy_zmien_" in response.headers["Content-Disposition"]

    text = response.data.decode("utf-8")
    assert text.startswith("\ufeff")
    lines = text.lstrip("\ufeff").strip().splitlines()
    assert lines[0] == "Čas;Používateľ;Typ;Akcia;Popis"
    assert any("Pridaná pobočka: Nitra" in line for line in lines)
    assert any("mgr_ab;Smeny;shift.create" in line for line in lines)

    manager_text = login(aw, "mgr_a", "managerpass1").get("/audit/export.csv").data.decode("utf-8")
    assert "Anna Prvá" in manager_text
    assert "Boris Druhý" not in manager_text and "Nitra" not in manager_text

    filtered = admin.get("/audit/export.csv?entity=branch").data.decode("utf-8")
    assert "Pridaná smena" not in filtered and "Nitra" in filtered


def test_navigation_link_is_shown_to_managers_and_admins_only(aw):
    for user, password in (("mgr_a", "managerpass1"), ("admin", "adminpass1")):
        assert 'href="/audit"' in login(aw, user, password).get("/").get_data(as_text=True)

    assert 'href="/audit"' not in login(aw, "emp_a", "emppass12").get("/my-schedule").get_data(as_text=True)


# ---------------------------------------------------------------------
# Čas, údržba, migrácia
# ---------------------------------------------------------------------

def test_times_are_shown_in_local_time(monkeypatch):
    from app.services.audit_service import to_local

    monkeypatch.delenv("APP_TIMEZONE", raising=False)

    assert to_local("2026-07-01 10:00:00") == "01.07.2026 12:00:00"   # letný čas
    assert to_local("2026-01-15 10:00:00") == "15.01.2026 11:00:00"   # zimný čas

    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    assert to_local("2026-07-01 10:00:00") == "01.07.2026 10:00:00"

    monkeypatch.setenv("APP_TIMEZONE", "Neexistuje/Zona")
    assert to_local("2026-07-01 10:00:00") == "01.07.2026 10:00:00"   # záloha: UTC

    assert to_local("nie-cas") == "nie-cas"
    assert to_local(None) == ""


def test_date_filter_uses_local_days(aw, monkeypatch):
    from app.extensions import db
    from app.orm_models import AuditLog
    from app.services.audit_service import get_audit_entries

    monkeypatch.setenv("APP_TIMEZONE", "Europe/Bratislava")

    with aw.app_context():
        # 22:30 UTC v lete = 00:30 už nasledujúceho dňa v Bratislave.
        db.session.add(AuditLog(
            created_at="2026-07-01 22:30:00", action="x.y", entity_type="shift",
            summary="neskorý večer",
        ))
        db.session.commit()

        on_second, _ = get_audit_entries(date_from=date(2026, 7, 2), date_to=date(2026, 7, 2))
        on_first, _ = get_audit_entries(date_from=date(2026, 7, 1), date_to=date(2026, 7, 1))

    assert [e.summary for e in on_second] == ["neskorý večer"]
    assert on_first == []


def test_prune_removes_only_old_entries(aw):
    from app.extensions import db
    from app.orm_models import AuditLog
    from app.services.audit_service import prune_older_than

    old = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")

    with aw.app_context():
        db.session.add_all([
            AuditLog(created_at=old, action="a.b", entity_type="shift", summary="stary"),
            AuditLog(created_at=recent, action="a.b", entity_type="shift", summary="novy"),
        ])
        db.session.commit()

        assert prune_older_than(30) == 1

    assert summaries(aw) == ["novy"]


def test_prune_command_validates_input(aw, monkeypatch, capsys):
    import sys

    import manage

    for argument in (None, "abc", "10"):
        monkeypatch.setattr(sys, "argv", ["manage.py", "audit-prune", *([argument] if argument else [])])

        with pytest.raises(SystemExit) as exit_info:
            manage.main()

        assert exit_info.value.code == 1

    assert "30 dní" in capsys.readouterr().out


def test_audit_log_migration_roundtrip(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "auditmig.db")

    with app.app_context():
        upgrade()
        inspector = sa.inspect(db.engine)
        assert "audit_log" in inspector.get_table_names()
        assert {"ix_audit_log_created_at", "ix_audit_log_branch_id", "ix_audit_log_entity_type"} <= {
            index["name"] for index in inspector.get_indexes("audit_log")
        }

        downgrade(revision="e6f7a8b9c021")
        assert "audit_log" not in sa.inspect(db.engine).get_table_names()
        assert "leave_requests" in sa.inspect(db.engine).get_table_names()

        upgrade()
        assert "audit_log" in sa.inspect(db.engine).get_table_names()
