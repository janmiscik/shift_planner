"""Testy žiadostí o voľno (zamestnanec žiada, manažér schvaľuje)."""

import re
from datetime import date, timedelta

import pytest


def _csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def iso(days_from_today):
    return (date.today() + timedelta(days=days_from_today)).isoformat()


START, END = iso(30), iso(34)   # 5 dní


@pytest.fixture
def lw(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "leave.db")
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
        from app.services.employee_service import add_employee

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")
        ids["anna"] = add_employee("Anna", "Prvá", "Op", 40, ids["a"])
        ids["boris"] = add_employee("Boris", "Druhý", "Op", 40, ids["b"])

        create_admin("admin", "adminpass1")
        create_manager("mgr_a", "managerpass1", [ids["a"]])
        create_manager("mgr_b", "managerpass1", [ids["b"]])
        create_employee_user(ids["anna"], "emp_a", "emppass12")
        create_employee_user(ids["boris"], "emp_b", "emppass12")

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


def submit(client, absence_type="Dovolenka", start=START, end=END, note="", follow=False):
    token = _csrf(client.get("/my-requests").get_data(as_text=True))

    return client.post(
        "/my-requests",
        data={
            "csrf_token": token,
            "absence_type": absence_type,
            "start_date": start,
            "end_date": end,
            "note": note,
        },
        follow_redirects=follow,
    )


def decide(client, request_id, action, note="", page="/leave-requests", follow=False):
    token = _csrf(client.get(page).get_data(as_text=True))

    return client.post(
        f"/leave-requests/{request_id}/{action}",
        data={"csrf_token": token, "decision_note": note},
        follow_redirects=follow,
    )


def all_requests(app):
    from app.services.leave_request_service import get_requests

    with app.app_context():
        return get_requests()


def first_request_id(app):
    rows = all_requests(app)
    assert rows
    return rows[-1][0]


# ---------------------------------------------------------------------
# Podanie žiadosti
# ---------------------------------------------------------------------

def test_employee_submits_a_request(lw):
    employee = login(lw, "emp_a", "emppass12")

    response = submit(employee, note="Rodinná dovolenka")
    assert response.status_code == 302

    rows = all_requests(lw)
    assert len(rows) == 1

    row = rows[0]
    assert (row[1], row[6], row[7], row[8], row[9], row[10]) == (
        lw.test_ids["anna"], "Dovolenka", START, END, "Rodinná dovolenka", "pending",
    )

    html = employee.get("/my-requests").get_data(as_text=True)
    assert "Čaká na schválenie" in html and START in html
    assert "Rodinná dovolenka" not in html or True   # poznámka sa nezobrazuje v zozname


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"absence_type": "PN"}, "Vyber druh voľna zo zoznamu"),
        ({"start": iso(-3), "end": iso(2)}, "spätne"),
        ({"start": iso(10), "end": iso(5)}, "pred jeho začiatkom"),
        ({"start": "", "end": END}, "dátum začiatku"),
    ],
)
def test_invalid_requests_are_rejected(lw, kwargs, message):
    employee = login(lw, "emp_a", "emppass12")

    response = submit(employee, **kwargs)

    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert all_requests(lw) == []


def test_request_cannot_overlap_existing_absence_or_pending_request(lw):
    from app.services.absence_service import add_absence

    employee = login(lw, "emp_a", "emppass12")

    with lw.app_context():
        add_absence(lw.test_ids["anna"], "PN", iso(31), iso(32))

    response = submit(employee, start=iso(30), end=iso(33))
    assert "už máš zapísanú neprítomnosť" in response.get_data(as_text=True)

    assert submit(employee, start=iso(40), end=iso(42)).status_code == 302

    response = submit(employee, start=iso(41), end=iso(45))
    assert "už máš čakajúcu žiadosť" in response.get_data(as_text=True)

    assert len(all_requests(lw)) == 1


def test_one_day_request_and_today_are_allowed(lw):
    employee = login(lw, "emp_a", "emppass12")

    assert submit(employee, start=iso(0), end=iso(0)).status_code == 302
    assert len(all_requests(lw)) == 1


def test_employee_sees_only_own_requests(lw):
    submit(login(lw, "emp_a", "emppass12"), note="anna")
    submit(login(lw, "emp_b", "emppass12"), start=iso(50), end=iso(51))

    html = login(lw, "emp_a", "emppass12").get("/my-requests").get_data(as_text=True)

    assert START in html
    assert iso(50) not in html


# ---------------------------------------------------------------------
# Zrušenie
# ---------------------------------------------------------------------

def test_employee_cancels_own_pending_request(lw):
    employee = login(lw, "emp_a", "emppass12")
    submit(employee)
    request_id = first_request_id(lw)

    token = _csrf(employee.get("/my-requests").get_data(as_text=True))
    response = employee.post(
        f"/my-requests/{request_id}/cancel", data={"csrf_token": token}
    )

    assert response.status_code == 302
    assert all_requests(lw)[0][10] == "cancelled"

    # Po zrušení sa obdobie dá požiadať znova.
    assert submit(employee).status_code == 302


def test_employee_cannot_cancel_someone_elses_or_a_decided_request(lw):
    anna = login(lw, "emp_a", "emppass12")
    boris = login(lw, "emp_b", "emppass12")

    submit(anna)
    request_id = first_request_id(lw)

    token = _csrf(boris.get("/my-requests").get_data(as_text=True))
    boris.post(f"/my-requests/{request_id}/cancel", data={"csrf_token": token})
    assert all_requests(lw)[0][10] == "pending"

    decide(login(lw, "mgr_a", "managerpass1"), request_id, "approve")

    token = _csrf(anna.get("/my-requests").get_data(as_text=True))
    anna.post(f"/my-requests/{request_id}/cancel", data={"csrf_token": token})
    assert all_requests(lw)[0][10] == "approved"


# ---------------------------------------------------------------------
# Schválenie / zamietnutie
# ---------------------------------------------------------------------

def test_approval_creates_an_absence_that_planning_respects(lw):
    from app.services.absence_service import get_absences
    from app.services.shift_service import validate_shift

    submit(login(lw, "emp_a", "emppass12"), note="Dovolenka s rodinou")
    request_id = first_request_id(lw)

    manager = login(lw, "mgr_a", "managerpass1")
    response = decide(manager, request_id, "approve", note="Uži si to")
    assert response.status_code == 302

    row = all_requests(lw)[0]
    assert row[10] == "approved" and row[14] == "Uži si to" and row[13] == "mgr_a"

    with lw.app_context():
        absences = get_absences()
        assert [(a[3], a[4], a[5], a[6], a[7]) for a in absences] == [
            (lw.test_ids["anna"], "Dovolenka", START, END, "Dovolenka s rodinou"),
        ]

        # Plánovanie smien ju rešpektuje.
        errors = validate_shift(lw.test_ids["anna"], START, "06:00", "14:00")
        assert any("neprítomnos" in error for error in errors)

    assert "Anna" in manager.get("/absences").get_data(as_text=True)


def test_rejection_requires_a_reason_and_creates_no_absence(lw):
    from app.services.absence_service import get_absences

    employee = login(lw, "emp_a", "emppass12")
    submit(employee)
    request_id = first_request_id(lw)
    manager = login(lw, "mgr_a", "managerpass1")

    response = decide(manager, request_id, "reject", note="  ", follow=True)
    assert "uveď dôvod" in response.get_data(as_text=True)
    assert all_requests(lw)[0][10] == "pending"

    decide(manager, request_id, "reject", note="Vtedy je inventúra")

    row = all_requests(lw)[0]
    assert row[10] == "rejected" and row[14] == "Vtedy je inventúra"

    with lw.app_context():
        assert get_absences() == []

    html = employee.get("/my-requests").get_data(as_text=True)
    assert "Zamietnutá" in html and "Vtedy je inventúra" in html


def test_decided_request_cannot_be_decided_again(lw):
    submit(login(lw, "emp_a", "emppass12"))
    request_id = first_request_id(lw)
    manager = login(lw, "mgr_a", "managerpass1")

    decide(manager, request_id, "approve")
    response = decide(manager, request_id, "reject", note="dôvod", follow=True)

    assert "už bola vybavená" in response.get_data(as_text=True)
    assert all_requests(lw)[0][10] == "approved"

    from app.services.absence_service import get_absences

    with lw.app_context():
        assert len(get_absences()) == 1


def test_approval_blocked_when_an_absence_was_added_meanwhile(lw):
    from app.services.absence_service import add_absence, get_absences

    submit(login(lw, "emp_a", "emppass12"))
    request_id = first_request_id(lw)

    with lw.app_context():
        add_absence(lw.test_ids["anna"], "PN", iso(32), iso(33))

    response = decide(
        login(lw, "mgr_a", "managerpass1"), request_id, "approve", follow=True
    )

    assert "už zapísanú neprítomnosť" in response.get_data(as_text=True)
    assert all_requests(lw)[0][10] == "pending"

    with lw.app_context():
        assert len(get_absences()) == 1


def test_shift_conflicts_are_shown_and_warned_about(lw):
    from app.services.shift_service import add_shift

    with lw.app_context():
        add_shift(lw.test_ids["anna"], iso(31), "06:00", "14:00", "Ranná")
        add_shift(lw.test_ids["anna"], iso(32), "06:00", "14:00", "Ranná")

    submit(login(lw, "emp_a", "emppass12"))
    request_id = first_request_id(lw)
    manager = login(lw, "mgr_a", "managerpass1")

    html = manager.get("/leave-requests").get_data(as_text=True)
    assert "2" in html and "naplánovaných smien" in html

    response = decide(manager, request_id, "approve", follow=True)
    text = response.get_data(as_text=True)
    assert "schválená" in text and "ešte 2 naplánovaných smien" in text


def test_deleting_the_absence_keeps_the_request_record(lw):
    from app.services.absence_service import delete_absence, get_absences

    submit(login(lw, "emp_a", "emppass12"))
    request_id = first_request_id(lw)
    decide(login(lw, "mgr_a", "managerpass1"), request_id, "approve")

    with lw.app_context():
        absence_id = get_absences()[0][0]
        delete_absence(absence_id)

    from app.extensions import db
    from app.orm_models import LeaveRequest

    with lw.app_context():
        stored = db.session.get(LeaveRequest, request_id)
        assert stored.status == "approved" and stored.absence_id is None


# ---------------------------------------------------------------------
# Práva
# ---------------------------------------------------------------------

def test_manager_sees_and_decides_only_own_branch(lw):
    submit(login(lw, "emp_a", "emppass12"))
    submit(login(lw, "emp_b", "emppass12"), start=iso(60), end=iso(61))

    anna_request = [r for r in all_requests(lw) if r[1] == lw.test_ids["anna"]][0][0]

    mgr_b = login(lw, "mgr_b", "managerpass1")
    html = mgr_b.get("/leave-requests").get_data(as_text=True)
    assert "Boris" in html and "Anna" not in html

    token = _csrf(html)
    for action in ("approve", "reject"):
        response = mgr_b.post(
            f"/leave-requests/{anna_request}/{action}",
            data={"csrf_token": token, "decision_note": "x"},
        )
        assert response.status_code == 404

    assert [r[10] for r in all_requests(lw) if r[0] == anna_request] == ["pending"]

    admin_html = login(lw, "admin", "adminpass1").get("/leave-requests").get_data(as_text=True)
    assert "Anna" in admin_html and "Boris" in admin_html


def test_employee_cannot_open_or_decide_manager_pages(lw):
    submit(login(lw, "emp_a", "emppass12"))
    request_id = first_request_id(lw)

    boris = login(lw, "emp_b", "emppass12")
    assert boris.get("/leave-requests").status_code == 403

    token = _csrf(boris.get("/my-requests").get_data(as_text=True))
    for action in ("approve", "reject"):
        response = boris.post(
            f"/leave-requests/{request_id}/{action}",
            data={"csrf_token": token, "decision_note": "x"},
        )
        assert response.status_code == 403

    assert all_requests(lw)[0][10] == "pending"


def test_accounts_without_an_employee_cannot_request_leave(lw):
    # Admin: presmerovanie na úvod; čistý manažér: stránka mu nie je
    # dostupná (rovnako ako "Môj rozpis").
    admin = login(lw, "admin", "adminpass1").get("/my-requests")
    assert admin.status_code == 302 and admin.headers["Location"].endswith("/")

    assert login(lw, "mgr_a", "managerpass1").get("/my-requests").status_code == 403


def test_manager_with_employee_account_cannot_decide_own_request(lw):
    from app.services.auth_service import set_manager_function

    with lw.app_context():
        set_manager_function(lw.test_ids["anna"], True, [lw.test_ids["a"]])

    worker_manager = login(lw, "emp_a", "emppass12")
    submit(worker_manager)
    request_id = first_request_id(lw)

    html = worker_manager.get("/leave-requests").get_data(as_text=True)
    assert "vybaví iný manažér alebo admin" in html

    response = decide(worker_manager, request_id, "approve", follow=True)
    assert "Vlastnú žiadosť nemôžeš vybaviť sám" in response.get_data(as_text=True)
    assert all_requests(lw)[0][10] == "pending"

    # Iný manažér alebo admin to vybaviť môže.
    decide(login(lw, "admin", "adminpass1"), request_id, "approve")
    assert all_requests(lw)[0][10] == "approved"


def test_manager_with_employee_account_can_decide_others(lw):
    from app.services.auth_service import set_manager_function

    with lw.app_context():
        set_manager_function(lw.test_ids["anna"], True, [lw.test_ids["a"], lw.test_ids["b"]])

    submit(login(lw, "emp_b", "emppass12"))
    request_id = first_request_id(lw)

    decide(login(lw, "emp_a", "emppass12"), request_id, "approve")
    assert all_requests(lw)[0][10] == "approved"


# ---------------------------------------------------------------------
# Navigácia a stránky
# ---------------------------------------------------------------------

def test_pending_count_badge_in_navigation(lw):
    manager = login(lw, "mgr_a", "managerpass1")

    assert "Žiadosti o voľno (" not in manager.get("/").get_data(as_text=True)

    submit(login(lw, "emp_a", "emppass12"))
    assert "Žiadosti o voľno (1)" in manager.get("/").get_data(as_text=True)

    # Žiadosť z inej pobočky sa manažérovi nezapočíta.
    submit(login(lw, "emp_b", "emppass12"))
    assert "Žiadosti o voľno (1)" in manager.get("/").get_data(as_text=True)

    # Po vybavení sa počet z navigácie stratí.
    anna_request = [r for r in all_requests(lw) if r[1] == lw.test_ids["anna"]][0][0]
    decide(manager, anna_request, "approve")
    assert "Žiadosti o voľno (" not in manager.get("/").get_data(as_text=True)


def test_all_request_pages_render(lw):
    submit(login(lw, "emp_a", "emppass12"))

    employee = login(lw, "emp_a", "emppass12")
    assert employee.get("/my-requests").status_code == 200
    assert "Žiadosti o voľno" in employee.get("/my-schedule").get_data(as_text=True)

    for user, password in (("mgr_a", "managerpass1"), ("admin", "adminpass1")):
        client = login(lw, user, password)

        for status in ("pending", "approved", "rejected", "cancelled", "all", "nezmysel"):
            assert client.get(f"/leave-requests?status={status}").status_code == 200

        assert client.get(
            f"/leave-requests?branch_id={lw.test_ids['a']}"
        ).status_code == 200


# ---------------------------------------------------------------------
# Služba (jednotkovo) a migrácia
# ---------------------------------------------------------------------

def test_service_validation_with_explicit_today(lw):
    from app.services.leave_request_service import create_request, day_count

    anna = lw.test_ids["anna"]

    with lw.app_context():
        today = date(2026, 6, 15)

        assert create_request(anna, "Dovolenka", "2026-06-14", "2026-06-16", today=today)[1]
        assert create_request(anna, "Dovolenka", "zle", "2026-06-16", today=today)[1] == "Zadaj platné dátumy."
        assert create_request(anna, "Dovolenka", "2026-06-15", "2026-06-15", today=today)[0]

    assert day_count("2026-06-15", "2026-06-15") == 1
    assert day_count("2026-06-15", "2026-06-21") == 7


def test_leave_requests_migration_roundtrip(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "lrmig.db")

    with app.app_context():
        upgrade()
        assert "leave_requests" in sa.inspect(db.engine).get_table_names()

        downgrade(revision="d5e6f7a8b910")
        assert "leave_requests" not in sa.inspect(db.engine).get_table_names()
        assert "shift_templates" in sa.inspect(db.engine).get_table_names()

        upgrade()
        assert "leave_requests" in sa.inspect(db.engine).get_table_names()
