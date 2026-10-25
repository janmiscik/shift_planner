"""Testy dostupnosti zamestnancov (kedy nemôžu pracovať)."""

import re
from datetime import date, timedelta
from types import SimpleNamespace as Rule

import pytest

from app.services.availability_service import (
    create_rule,
    describe_rule,
    rule_conflicts,
)


def _csrf(html):
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa nenašiel")


TODAY = date.today()
MON = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)   # budúci pondelok
D = [MON + timedelta(days=n) for n in range(7)]                # pondelok ... nedeľa


def weekly(weekday, time_from=None, time_to=None, date_from=None, date_to=None, note=None):
    return Rule(weekday=weekday, date_from=date_from, date_to=date_to,
                time_from=time_from, time_to=time_to, note=note)


def once(date_from, date_to=None, time_from=None, time_to=None, note=None):
    return Rule(weekday=None, date_from=date_from, date_to=date_to or date_from,
                time_from=time_from, time_to=time_to, note=note)


# ---------------------------------------------------------------------
# Popis pravidiel
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "rule, expected",
    [
        (weekly(2, "14:00", "22:00"), "Každú stredu 14:00–22:00"),
        (weekly(0), "Každý pondelok celý deň"),
        (weekly(5, date_from="2026-10-01", date_to="2026-12-31"),
         "Každú sobotu celý deň (od 01.10.2026 do 31.12.2026)"),
        (weekly(6, date_from="2026-10-01"), "Každú nedeľu celý deň (od 01.10.2026)"),
        (weekly(1, date_to="2026-12-31"), "Každý utorok celý deň (do 31.12.2026)"),
        (once("2026-10-05"), "05.10.2026 celý deň"),
        (once("2026-10-05", "2026-10-07"), "05.10.2026 – 07.10.2026 celý deň"),
        (once("2026-10-05", time_from="08:00", time_to="12:00"), "05.10.2026 08:00–12:00"),
    ],
)
def test_describe_rule(rule, expected):
    assert describe_rule(rule) == expected


def test_describe_rule_with_note():
    assert describe_rule(weekly(0, note="škola"), with_note=True) == "Každý pondelok celý deň - škola"
    assert describe_rule(weekly(0), with_note=True) == "Každý pondelok celý deň"


# ---------------------------------------------------------------------
# Prekrývanie so smenou
# ---------------------------------------------------------------------

MONDAY, TUESDAY, FRIDAY, SATURDAY = "2026-10-05", "2026-10-06", "2026-10-09", "2026-10-10"


@pytest.mark.parametrize(
    "rule, shift_date, start, end, expected",
    [
        # opakované, celý deň
        (weekly(0), MONDAY, "06:00", "14:00", True),
        (weekly(0), TUESDAY, "06:00", "14:00", False),
        # časové okno: prekrytie / dotyk / mimo
        (weekly(0, "14:00", "22:00"), MONDAY, "06:00", "14:00", False),     # dotyk
        (weekly(0, "14:00", "22:00"), MONDAY, "13:00", "15:00", True),
        (weekly(0, "14:00", "22:00"), MONDAY, "22:00", "23:00", False),     # dotyk
        (weekly(0, "14:00", "22:00"), MONDAY, "15:00", "16:00", True),      # vo vnútri
        (weekly(0, "14:00", "22:00"), MONDAY, "10:00", "23:00", True),      # obklopuje
        # platnosť
        (weekly(0, date_from="2026-10-12"), MONDAY, "06:00", "14:00", False),
        (weekly(0, date_from="2026-10-05"), MONDAY, "06:00", "14:00", True),
        (weekly(0, date_to="2026-10-04"), MONDAY, "06:00", "14:00", False),
        (weekly(0, date_to="2026-10-05"), MONDAY, "06:00", "14:00", True),
        # jednorazové obdobie
        (once("2026-10-05", "2026-10-07"), TUESDAY, "06:00", "14:00", True),
        (once("2026-10-05", "2026-10-07"), "2026-10-08", "06:00", "14:00", False),
        (once("2026-10-05", "2026-10-07"), "2026-10-04", "06:00", "14:00", False),
        (once("2026-10-05", time_from="08:00", time_to="12:00"), MONDAY, "12:00", "16:00", False),
        (once("2026-10-05", time_from="08:00", time_to="12:00"), MONDAY, "11:00", "16:00", True),
        # nočná smena cez polnoc: piatok 22:00 -> sobota 06:00
        (weekly(5, "00:00", "08:00"), FRIDAY, "22:00", "06:00", True),     # sobotné ráno
        (weekly(5, "08:00", "20:00"), FRIDAY, "22:00", "06:00", False),
        (weekly(4, "20:00", "23:00"), FRIDAY, "22:00", "06:00", True),     # piatkový večer
        (weekly(5), FRIDAY, "22:00", "06:00", True),                       # sobota celý deň
        (weekly(6), FRIDAY, "22:00", "06:00", False),
        (once("2026-10-10"), FRIDAY, "22:00", "06:00", True),              # nasledujúci deň
        (once("2026-10-09"), "2026-10-08", "22:00", "06:00", True),        # do dňa pravidla
        (once("2026-10-10"), "2026-10-08", "22:00", "06:00", False),
        # smena od polnoci
        (weekly(0, "00:00", "06:00"), MONDAY, "00:00", "08:00", True),
        (weekly(0, "06:00", "12:00"), MONDAY, "00:00", "06:00", False),
    ],
)
def test_rule_conflicts(rule, shift_date, start, end, expected):
    assert rule_conflicts(rule, shift_date, start, end) is expected


# ---------------------------------------------------------------------
# Vytváranie pravidiel
# ---------------------------------------------------------------------

@pytest.fixture
def aw(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "avail.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    ids = {}

    with app.app_context():
        db.create_all()

        from app.services.auth_service import create_admin, create_employee_user, create_manager
        from app.services.branch_service import add_branch
        from app.services.employee_service import add_employee
        from app.services.shift_service import add_shift

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")
        ids["anna"] = add_employee("Anna", "Prvá", "Op", 40, ids["a"])
        ids["cyril"] = add_employee("Cyril", "Tretí", "Op", 40, ids["a"])
        ids["boris"] = add_employee("Boris", "Druhý", "Op", 40, ids["b"])

        create_employee_user(ids["anna"], "emp_anna", "emppass12")
        create_employee_user(ids["cyril"], "emp_cyril", "emppass12")
        create_employee_user(ids["boris"], "emp_boris", "emppass12")
        create_admin("admin", "adminpass1")
        create_manager("mgr_a", "managerpass1", [ids["a"]])
        create_manager("mgr_b", "managerpass1", [ids["b"]])

        # Anna v pondelok 06-14 aj v utorok 06-14.
        ids["s_mon"] = add_shift(ids["anna"], D[0].isoformat(), "06:00", "14:00", "Ranná", published=True)
        ids["s_tue"] = add_shift(ids["anna"], D[1].isoformat(), "06:00", "14:00", "Ranná", published=True)

    app.test_ids = ids

    return app


def make(app, employee="anna", **kwargs):
    with app.app_context():
        return create_rule(app.test_ids[employee], **kwargs)


def rule_rows(app, employee=None):
    from app.services.availability_service import get_rule_rows

    with app.app_context():
        return [
            (row[0].id, row[0].weekday, row[0].date_from, row[0].date_to,
             row[0].time_from, row[0].time_to, row[0].note, row[1])
            for row in get_rule_rows(employee_id=app.test_ids[employee] if employee else None)
        ]


def test_create_weekly_and_one_off_rules(aw):
    rule_id, error = make(aw, weekday=0, time_from="8:00", time_to="12:30", note=" škola ")
    assert error is None

    rows = rule_rows(aw, "anna")
    assert rows[0][1:7] == (0, None, None, "08:00", "12:30", "škola")      # čas aj poznámka upravené

    make(aw, date_from=D[2].isoformat(), date_to=D[3].isoformat())
    make(aw, date_from=D[4].isoformat())                                    # jeden deň

    rows = rule_rows(aw, "anna")
    assert [r[1] for r in rows] == [0, None, None]                          # opakované prvé
    assert rows[2][2] == rows[2][3] == D[4].isoformat()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"weekday": 9}, "deň v týždni"),
        ({"weekday": -1}, "deň v týždni"),
        ({}, "Zadaj dátum"),
        ({"date_from": "nie-datum"}, "platné dátumy"),
        ({"date_from": "2026-10-07", "date_to": "2026-10-05"}, "pred začiatkom"),
        ({"weekday": 0, "date_from": "2999-02-01", "date_to": "2999-01-01"}, "pred jej začiatkom"),
        ({"date_from": (TODAY - timedelta(days=3)).isoformat()}, "už skončilo"),
        ({"weekday": 0, "date_to": (TODAY - timedelta(days=1)).isoformat()}, "už skončilo"),
        ({"weekday": 0, "time_from": "08:00"}, "od aj do"),
        ({"weekday": 0, "time_to": "08:00"}, "od aj do"),
        ({"weekday": 0, "time_from": "8h", "time_to": "10:00"}, "platné časy"),
        ({"weekday": 0, "time_from": "25:00", "time_to": "26:00"}, "platné časy"),
        ({"weekday": 0, "time_from": "10:00", "time_to": "10:00"}, "neskôr než"),
        ({"weekday": 0, "time_from": "22:00", "time_to": "06:00"}, "dve pravidlá"),
        ({"weekday": 0, "note": "x" * 256}, "255 znakov"),
    ],
)
def test_invalid_rules_are_rejected(aw, kwargs, message):
    rule_id, error = make(aw, **kwargs)

    assert rule_id is None and message in error
    assert rule_rows(aw) == []


def test_duplicate_and_limit(aw):
    from app.services.availability_service import MAX_RULES_PER_EMPLOYEE

    assert make(aw, weekday=0, time_from="08:00", time_to="12:00")[1] is None
    assert "už máš zapísané" in make(aw, weekday=0, time_from="08:00", time_to="12:00")[1]
    assert make(aw, weekday=0, time_from="08:00", time_to="13:00")[1] is None   # iné okno

    for n in range(MAX_RULES_PER_EMPLOYEE - 2):
        day = TODAY + timedelta(days=100 + n)
        assert make(aw, date_from=day.isoformat())[1] is None

    assert "50 pravidiel" in make(aw, weekday=3)[1]


def test_unknown_employee(aw):
    with aw.app_context():
        assert create_rule(999999, weekday=0) == (None, "Zamestnanec neexistuje.")


def test_expired_rules_are_hidden_and_ignored(aw):
    from app.extensions import db
    from app.orm_models import AvailabilityRule
    from app.services.availability_service import get_rule_rows, rules_for_employees

    ids = aw.test_ids

    with aw.app_context():
        db.session.add(AvailabilityRule(
            employee_id=ids["anna"], weekday=None,
            date_from=(TODAY - timedelta(days=10)).isoformat(),
            date_to=(TODAY - timedelta(days=5)).isoformat(),
        ))
        db.session.commit()

        assert get_rule_rows(employee_id=ids["anna"]) == []
        assert len(get_rule_rows(employee_id=ids["anna"], include_expired=True)) == 1
        assert rules_for_employees([ids["anna"]]) == {ids["anna"]: []}


def test_delete_rule_requires_ownership(aw):
    from app.services.availability_service import delete_rule

    rule_id, _ = make(aw, weekday=0)

    with aw.app_context():
        assert delete_rule(rule_id, employee_id=aw.test_ids["cyril"]) is False
        assert delete_rule(999999) is False

    assert len(rule_rows(aw)) == 1

    with aw.app_context():
        assert delete_rule(rule_id, employee_id=aw.test_ids["anna"]) is True

    assert rule_rows(aw) == []


def test_conflicts_for_shifts_only_future_and_only_conflicting(aw):
    from app.services.availability_service import conflicts_for_shifts
    from app.services.shift_service import get_shifts

    make(aw, weekday=0, note="škola")               # pondelok celý deň

    with aw.app_context():
        rows = get_shifts()
        result = conflicts_for_shifts(rows)
        assert list(result) == [aw.test_ids["s_mon"]]
        assert "Každý pondelok celý deň - škola" in result[aw.test_ids["s_mon"]][0]

        # Minulé smeny sa preskočia.
        assert conflicts_for_shifts(rows, from_date=(D[0] + timedelta(days=1)).isoformat()) == {}


# ---------------------------------------------------------------------
# Stránky
# ---------------------------------------------------------------------

def login(app, username, password):
    client = app.test_client()
    token = _csrf(client.get("/login").get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": username, "password": password})
    return client


def post(client, page, url, data=None, follow=False):
    token = _csrf(client.get(page).get_data(as_text=True))

    return client.post(url, data={"csrf_token": token, **(data or {})}, follow_redirects=follow)


def test_employee_adds_and_deletes_rules(aw):
    anna = login(aw, "emp_anna", "emppass12")

    response = post(anna, "/my-availability", "/my-availability", {
        "kind": "weekly", "weekday": "2", "time_from": "14:00", "time_to": "22:00",
        "note": "škola",
    })
    assert response.status_code == 302

    post(anna, "/my-availability", "/my-availability", {
        "kind": "weekly", "weekday": "0", "all_day": "1", "time_from": "08:00", "time_to": "09:00",
    })
    post(anna, "/my-availability", "/my-availability", {
        "kind": "date", "date_from": D[3].isoformat(), "date_to": D[4].isoformat(), "all_day": "1",
    })

    rows = rule_rows(aw, "anna")
    assert len(rows) == 3
    assert rows[0][1:6] == (0, None, None, None, None)                  # "celý deň" ignoruje časy

    html = anna.get("/my-availability").get_data(as_text=True)
    assert "Každú stredu 14:00–22:00" in html and "škola" in html
    assert "Zrušiť" in html

    rule_id = rows[0][0]
    response = post(anna, "/my-availability", f"/my-availability/{rule_id}/delete")
    assert response.status_code == 302
    assert len(rule_rows(aw, "anna")) == 2


@pytest.mark.parametrize(
    "data, message",
    [
        ({"kind": "nieco"}, "opakované alebo jednorazové"),
        ({"kind": "weekly"}, "Vyber deň v týždni"),
        ({"kind": "date"}, "Zadaj dátum"),
        ({"kind": "date", "date_from": D[0].isoformat(), "time_from": "10:00"}, "od aj do"),
        ({"kind": "weekly", "weekday": "1", "time_from": "12:00", "time_to": "10:00"}, "neskôr než"),
    ],
)
def test_invalid_form_input_shows_a_message_and_saves_nothing(aw, data, message):
    anna = login(aw, "emp_anna", "emppass12")

    response = post(anna, "/my-availability", "/my-availability", data, follow=True)

    assert message in response.get_data(as_text=True)
    assert rule_rows(aw) == []


def test_employee_cannot_delete_someone_elses_rule(aw):
    rule_id, _ = make(aw, "cyril", weekday=0)
    anna = login(aw, "emp_anna", "emppass12")

    response = post(anna, "/my-availability", f"/my-availability/{rule_id}/delete", follow=True)

    assert "Pravidlo neexistuje" in response.get_data(as_text=True)
    assert len(rule_rows(aw, "cyril")) == 1


def test_manager_overview_is_scoped_and_read_only(aw):
    make(aw, "anna", weekday=0, note="škola")
    make(aw, "boris", weekday=1, note="druhá práca")

    mgr_a = login(aw, "mgr_a", "managerpass1").get("/availability").get_data(as_text=True)
    assert "Anna" in mgr_a and "škola" in mgr_a
    assert "Boris" not in mgr_a and "druhá práca" not in mgr_a

    admin = login(aw, "admin", "adminpass1").get("/availability").get_data(as_text=True)
    assert "Anna" in admin and "Boris" in admin

    only_b = login(aw, "admin", "adminpass1").get(f"/availability?branch_id={aw.test_ids['b']}").get_data(as_text=True)
    assert "Boris" in only_b and "Anna" not in only_b


def test_permissions(aw):
    assert login(aw, "emp_anna", "emppass12").get("/availability").status_code == 403
    assert login(aw, "mgr_a", "managerpass1").get("/my-availability").status_code == 403
    assert login(aw, "admin", "adminpass1").get("/my-availability").status_code == 302


def test_manager_with_employee_account_has_both_pages(aw):
    from app.services.auth_service import set_manager_function

    with aw.app_context():
        set_manager_function(aw.test_ids["anna"], True, [aw.test_ids["a"]])

    client = login(aw, "emp_anna", "emppass12")

    assert client.get("/my-availability").status_code == 200
    assert client.get("/availability").status_code == 200
    assert 'href="/my-availability"' in client.get("/").get_data(as_text=True)


def test_navigation_links(aw):
    assert 'href="/my-availability"' in login(aw, "emp_anna", "emppass12").get("/my-schedule").get_data(as_text=True)

    for user, password in (("mgr_a", "managerpass1"), ("admin", "adminpass1")):
        assert 'href="/availability"' in login(aw, user, password).get("/").get_data(as_text=True)


# ---------------------------------------------------------------------
# Upozornenia pri plánovaní (nikdy neblokujú)
# ---------------------------------------------------------------------

SHIFT_FORM = {"department_id": "", "shift_type": "Ranná"}


def add_shift_form(app, date_value, start, end, employee="anna"):
    return {
        "employee_id": str(app.test_ids[employee]), "shift_date": date_value.isoformat(),
        "start_time": start, "end_time": end, **SHIFT_FORM,
    }


def test_adding_a_shift_in_a_blocked_time_warns_but_saves(aw):
    from app.services.shift_service import get_shifts

    make(aw, weekday=3, time_from="06:00", time_to="10:00", note="škola")      # štvrtok
    manager = login(aw, "mgr_a", "managerpass1")

    response = post(manager, "/shifts/add", "/shifts/add", add_shift_form(aw, D[3], "08:00", "16:00"), follow=True)
    html = response.get_data(as_text=True)

    assert "flash-warning" in html
    assert "Zamestnanec uviedol, že v tomto čase nemôže pracovať" in html or "zamestnanec uviedol, že v tomto čase nemôže pracovať" in html
    assert "Každý štvrtok 06:00–10:00 - škola" in html

    with aw.app_context():
        assert D[3].isoformat() in [row[3] for row in get_shifts()]       # smena sa uložila


def test_no_warning_outside_the_blocked_time(aw):
    make(aw, weekday=3, time_from="06:00", time_to="10:00")
    manager = login(aw, "mgr_a", "managerpass1")

    response = post(manager, "/shifts/add", "/shifts/add", add_shift_form(aw, D[3], "10:00", "18:00"), follow=True)

    assert "flash-warning" not in response.get_data(as_text=True)


def test_editing_a_shift_into_a_blocked_time_warns(aw):
    make(aw, weekday=2, note="svadba")                                          # streda celý deň
    manager = login(aw, "mgr_a", "managerpass1")
    shift_id = aw.test_ids["s_mon"]

    response = post(
        manager, f"/shifts/edit/{shift_id}", f"/shifts/edit/{shift_id}",
        add_shift_form(aw, D[2], "06:00", "14:00"), follow=True,
    )

    assert "flash-warning" in response.get_data(as_text=True)
    assert "Každú stredu celý deň - svadba" in response.get_data(as_text=True)


def test_shift_list_marks_conflicting_future_shifts(aw):
    make(aw, weekday=0, note="škola")
    manager = login(aw, "mgr_a", "managerpass1")

    html = manager.get("/shifts").get_data(as_text=True)

    assert html.count("⚠ Zamestnanec v tom čase nemôže") == 1      # len pondelok, nie utorok
    assert "Každý pondelok celý deň - škola" in html


def test_calendar_marks_and_move_warns(aw):
    make(aw, weekday=0)                                              # pondelok celý deň
    make(aw, weekday=3, time_from="06:00", time_to="10:00")          # štvrtok ráno
    manager = login(aw, "mgr_a", "managerpass1")

    start, end = D[0].isoformat(), D[6].isoformat()
    events = manager.get(f"/api/shifts?start={start}&end={end}").get_json()
    by_date = {e["start"][:10]: e for e in events}

    assert by_date[D[0].isoformat()]["extendedProps"]["unavailable"] is True
    assert "shift-unavailable" in by_date[D[0].isoformat()]["className"]
    assert by_date[D[1].isoformat()]["extendedProps"]["unavailable"] is False

    assert "Zamestnanec v tom čase uviedol, že nemôže pracovať" in manager.get("/calendar").get_data(as_text=True)

    token = _csrf(manager.get("/calendar").get_data(as_text=True))

    # Presun na štvrtok ráno = upozornenie, ale presun prebehne.
    response = manager.post(
        f"/api/shifts/{aw.test_ids['s_tue']}/move",
        json={"shift_date": D[3].isoformat(), "start_time": "08:00", "end_time": "16:00"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert "nemôže pracovať" in response.get_json()["warnings"][0]

    # Presun na štvrtok popoludní (po okne 06-10) = bez upozornenia.
    response = manager.post(
        f"/api/shifts/{aw.test_ids['s_tue']}/move",
        json={"shift_date": D[3].isoformat(), "start_time": "12:00", "end_time": "20:00"},
        headers={"X-CSRFToken": token},
    )
    assert response.get_json() == {"ok": True}


def test_employees_never_see_availability_markers_in_the_calendar(aw, monkeypatch):
    monkeypatch.setenv("EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR", "1")
    make(aw, weekday=0)

    anna = login(aw, "emp_anna", "emppass12")
    events = anna.get(f"/api/shifts?start={D[0].isoformat()}&end={D[6].isoformat()}").get_json()

    assert events and not any("shift-unavailable" in e["className"] for e in events)
    assert not any(e["extendedProps"]["unavailable"] for e in events)


def test_week_plan_warns_but_still_creates(aw):
    from app.services.week_plan_service import entries_from_week, run_plan

    ids = aw.test_ids
    make(aw, weekday=0, note="škola")                                # pondelok celý deň

    with aw.app_context():
        entries = entries_from_week(MON, ids["a"])
        target = MON + timedelta(days=14)
        rows = run_plan(entries, target, commit=False)

    by_day = {row.entry.weekday: row for row in rows}

    assert by_day[0].status == "create"
    assert "Každý pondelok celý deň - škola" in by_day[0].warnings[0]
    assert by_day[1].status == "create" and by_day[1].warnings == []


def test_copy_week_preview_shows_the_warning(aw):
    make(aw, weekday=0, note="škola")
    manager = login(aw, "mgr_a", "managerpass1")

    response = post(manager, "/shifts/copy-week", "/shifts/copy-week", {
        "action": "preview", "branch_id": str(aw.test_ids["a"]),
        "source_date": MON.isoformat(), "target_date": (MON + timedelta(days=14)).isoformat(),
    })
    html = response.get_data(as_text=True)

    assert "Zamestnanec v tom čase uviedol, že nemôže pracovať" in html
    assert "Vytvorí sa" in html


def test_swap_approval_warns_the_manager_but_still_swaps(aw):
    from app.services.shift_swap_service import approve_swap, colleague_respond, create_swap

    ids = aw.test_ids
    make(aw, "cyril", weekday=0, note="druhá práca")                 # Cyril v pondelok nemôže

    with aw.app_context():
        swap_id, error = create_swap(ids["anna"], ids["cyril"], ids["s_mon"])
        assert error is None
        colleague_respond(swap_id, ids["cyril"], True)
        ok, messages = approve_swap(swap_id, None)

    assert ok is True
    assert len(messages) == 1
    assert "Cyril Tretí" in messages[0] and "druhá práca" in messages[0]

    from app.extensions import db
    from app.orm_models import Shift

    with aw.app_context():
        assert db.session.get(Shift, ids["s_mon"]).employee_id == ids["cyril"]      # výmena prebehla


def test_swap_approval_in_the_app_shows_a_warning_flash(aw):
    from app.services.shift_swap_service import colleague_respond, create_swap

    ids = aw.test_ids
    make(aw, "cyril", weekday=0, note="druhá práca")

    with aw.app_context():
        swap_id, _ = create_swap(ids["anna"], ids["cyril"], ids["s_mon"])
        colleague_respond(swap_id, ids["cyril"], True)

    manager = login(aw, "mgr_a", "managerpass1")
    response = post(manager, "/swap-requests", f"/swap-requests/{swap_id}/approve", follow=True)
    html = response.get_data(as_text=True)

    assert "Výmena bola schválená" in html
    assert "flash-warning" in html and "druhá práca" in html


# ---------------------------------------------------------------------
# Záznamy zmien, vzhľad, migrácia
# ---------------------------------------------------------------------

def test_rule_changes_are_audited_for_the_branch(aw):
    anna = login(aw, "emp_anna", "emppass12")

    post(anna, "/my-availability", "/my-availability", {
        "kind": "weekly", "weekday": "2", "time_from": "14:00", "time_to": "22:00", "note": "škola",
    })
    rule_id = rule_rows(aw, "anna")[0][0]
    post(anna, "/my-availability", f"/my-availability/{rule_id}/delete")

    manager_html = login(aw, "mgr_a", "managerpass1").get("/audit?entity=availability").get_data(as_text=True)

    assert "Zapísaná nedostupnosť: Anna Prvá - Každú stredu 14:00–22:00 - škola" in manager_html
    assert "Zrušená nedostupnosť: Anna Prvá" in manager_html
    assert "emp_anna" in manager_html

    # Manažér z inej pobočky to nevidí.
    assert "Anna" not in login(aw, "mgr_b", "managerpass1").get("/audit?entity=availability").get_data(as_text=True)


def test_warning_flash_style_exists(aw):
    css = aw.test_client().get("/static/style.css").get_data(as_text=True)

    assert ".flash-warning" in css and "--flash-warning-bg" in css


def test_availability_migration_roundtrip(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "availmig.db")

    with app.app_context():
        upgrade()
        assert "availability_rules" in sa.inspect(db.engine).get_table_names()

        downgrade(revision="b9c0d1e2f354")
        assert "availability_rules" not in sa.inspect(db.engine).get_table_names()
        assert "email_outbox" in sa.inspect(db.engine).get_table_names()

        upgrade()
        assert "availability_rules" in sa.inspect(db.engine).get_table_names()
