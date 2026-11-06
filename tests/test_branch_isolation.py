"""Izolácia pobočiek a objektov: systematická matica útokov.

Každá trasa s ID objektu sa vyskúša z pohľadu útočníka:
  - manažér pobočky A -> objekty pobočky B (a naopak),
  - zamestnanec pobočky A -> objekty pobočky B (a naopak),
  - zamestnanec -> objekty KOLEGU z vlastnej pobočky (trasy "len vlastné").
Pre každú požiadavku platí:
  1. stav databázy sa NEZMENÍ (okrem tabuliek audit_log a email_outbox),
  2. odpoveď neobsahuje žiadne údaje cudzej pobočky/kolegu,
  3. požiadavka nezlyhala len na neplatnom CSRF tokene (inak by test
     nič nedokazoval),
  4. cudzí objekt nikdy nevráti 200 (404/403/presmerovanie).
Pozitívne kontroly: tá istá požiadavka oprávneného používateľa na vlastné
objekty PREJDE (inak by matica mohla "prechádzať" len kvôli chybným
požiadavkám).

Test ``test_every_route_with_an_object_id_is_in_the_matrix`` zlyhá, ak
pribudne trasa s ID, ktorá v matici nie je.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

TODAY = date.today()
MON = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
PASSWORD = "pass12345"
IGNORED_TABLES = {"audit_log", "email_outbox"}


def iso(days):
    return (MON + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------
# Svet: dve pobočky s identickou štruktúrou
# ---------------------------------------------------------------------

def build_branch(key, token):
    """Vytvorí pobočku s oddelením, 4 zamestnancami (e1-e3 s účtami, e4
    bez), smenami, absenciou, žiadosťami, výmenami, šablónou a pravidlom
    dostupnosti. Všetky popisné údaje obsahujú ``token`` (na kontrolu
    úniku údajov). Vráti slovník ID."""

    from app.services.absence_service import add_absence
    from app.services.auth_service import (
        create_employee_user,
        create_manager,
        get_user_by_username,
    )
    from app.services.availability_service import create_rule
    from app.services.branch_service import add_branch
    from app.services.department_service import add_department
    from app.services.employee_department_service import add_employee_department
    from app.services.employee_service import add_employee
    from app.services.leave_request_service import create_request
    from app.services.shift_service import add_shift
    from app.services.shift_swap_service import colleague_respond, create_swap
    from app.services.week_plan_service import save_template_from_week

    ids = {"key": key, "token": token}

    ids["branch_id"] = add_branch(f"Pobocka-{token}")
    ids["department_id"] = add_department(f"Odd-{token}", branch_id=ids["branch_id"])

    for number, first in ((1, "Prvy"), (2, "Druhy"), (3, "Treti"), (4, "Stvrty")):
        ids[f"e{number}"] = add_employee(first, f"Priezv{token}", "Op", 40, ids["branch_id"])

    for number in (1, 2, 3):
        create_employee_user(ids[f"e{number}"], f"emp_{key}{number}", PASSWORD)
        ids[f"u{number}"] = get_user_by_username(f"emp_{key}{number}").id

    create_manager(f"mgr_{key}", PASSWORD, [ids["branch_id"]])
    ids["user_id"] = get_user_by_username(f"mgr_{key}").id

    ids["employee_id"] = ids["e1"]
    ids["employee_no_account_id"] = ids["e4"]
    add_employee_department(ids["e1"], ids["department_id"], 40)

    from app.extensions import db
    from app.orm_models import EmployeeDepartment

    ids["assignment_id"] = db.session.query(EmployeeDepartment).filter_by(
        employee_id=ids["e1"]
    ).one().id

    def shift(employee, offset, published=True):
        return add_shift(
            ids[employee], iso(offset), "06:00", "14:00", "Ranná", published=published
        )

    ids["shift_id"] = shift("e1", 0)
    ids["shift_e2"] = shift("e2", 1)
    ids["shift_e3"] = shift("e3", 2)
    ids["draft_shift_id"] = shift("e1", 3, published=False)

    ids["absence_id"] = add_absence(
        ids["e1"], "Dovolenka", iso(20), iso(21), note=f"abs-{token}"
    )

    future = lambda n: (TODAY + timedelta(days=n)).isoformat()
    ids["request_id"], _ = create_request(ids["e2"], "Dovolenka", future(30), future(31), f"dov-{token}")

    # Výmena Y: e1 -> e2, kolega prijal (čaká na manažéra).
    ids["swap_id"], _ = create_swap(ids["e1"], ids["e2"], ids["shift_id"], note=f"vym-{token}")
    colleague_respond(ids["swap_id"], ids["e2"], True)

    # Výmena X: e2 -> e3, čaká na kolegu (e3).
    ids["swap_id_peer"], _ = create_swap(ids["e2"], ids["e3"], ids["shift_e2"])

    ids["template_id"], _ = save_template_from_week(f"Sablona-{token}", ids["branch_id"], MON)
    ids["rule_id"], _ = create_rule(ids["e2"], weekday=3, note=f"dost-{token}")

    for name, value in {
        "monday": iso(0), "tuesday": iso(1), "wednesday": iso(2),
        "target_monday": iso(14), "far_date": future(60),
    }.items():
        ids[name] = value

    return ids


def build_world(tmp_path):
    from app.extensions import db
    from app.services.auth_service import create_admin
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "isolation.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    with app.app_context():
        db.create_all()
        create_admin("admin", PASSWORD)

        app.worlds = {
            "a": build_branch("a", "AAATOKEN"),
            "b": build_branch("b", "BBBTOKEN"),
        }

    return app


@pytest.fixture(scope="module")
def attack_app(tmp_path_factory):
    import os

    previous = os.environ.get("EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR")
    os.environ["EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR"] = "1"     # čo najpermisívnejšie nastavenie

    try:
        yield build_world(tmp_path_factory.mktemp("isolation"))
    finally:
        if previous is None:
            os.environ.pop("EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR", None)
        else:
            os.environ["EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR"] = previous


@pytest.fixture
def fresh_app(tmp_path):
    return build_world(tmp_path)


# ---------------------------------------------------------------------
# Pomocné funkcie
# ---------------------------------------------------------------------

def csrf_from(html):
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa nenašiel")


def login(app, username):
    client = app.test_client()
    token = csrf_from(client.get("/login").get_data(as_text=True))
    response = client.post(
        "/login", data={"csrf_token": token, "username": username, "password": PASSWORD}
    )
    assert response.status_code == 302, username

    return client


def token_for(client, username):
    page = "/my-schedule" if username.startswith("emp_") else "/shifts"

    return csrf_from(client.get(page).get_data(as_text=True))


def snapshot(app):
    import sqlalchemy as sa

    from app.extensions import db

    with app.app_context():
        result = {}

        for table in db.metadata.sorted_tables:
            if table.name in IGNORED_TABLES:
                continue

            order = [column for column in table.primary_key.columns] or list(table.columns)
            rows = db.session.execute(sa.select(table).order_by(*order)).all()
            result[table.name] = [tuple(map(repr, row)) for row in rows]

        db.session.rollback()

        return result


@dataclass
class Req:
    method: str
    url: str
    data: dict = field(default_factory=dict)
    kind: str = "form"                  # "form" alebo "json"
    legit_status: int = 200             # očakávaný stav pri oprávnenom používateľovi (GET)


@dataclass
class Recipe:
    requests: list
    legit: str = "mgr"                  # kto ju smie vykonať: mgr | admin | e2 | e3
    legit_changes_state: bool = True    # pozitívna kontrola: POST zmení stav / GET vráti 200


def expand(template, ids):
    if isinstance(template, dict):
        return {key: expand(value, ids) for key, value in template.items()}

    if isinstance(template, str):
        return template.format(**ids)

    return template


def send(client, request, ids, token):
    url = expand(request.url, ids)
    data = expand(request.data, ids)

    if request.kind == "json":
        return client.post(url, json=data, headers={"X-CSRFToken": token})

    if request.method == "POST":
        return client.post(url, data={"csrf_token": token, **data})

    return client.get(url)


SHIFT_FORM = {
    "employee_id": "{employee_id}", "department_id": "", "shift_date": "{tuesday}",
    "start_time": "06:00", "end_time": "14:00", "shift_type": "Ranná",
}

RECIPES = {
    # -------- manažér (vlastná pobočka) --------
    "delete_absence_page": Recipe([Req("POST", "/absences/delete/{absence_id}")]),
    "edit_absence_page": Recipe([
        Req("GET", "/absences/edit/{absence_id}"),
        Req("POST", "/absences/edit/{absence_id}", {
            "employee_id": "{employee_id}", "absence_type": "PN",
            "start_date": "{far_date}", "end_date": "{far_date}", "note": "x",
        }),
    ]),
    "api_employee_departments": Recipe([Req("GET", "/api/employees/{employee_id}/departments")]),
    "api_move_shift": Recipe([
        Req("POST", "/api/shifts/{shift_id}/move",
            {"shift_date": "{wednesday}", "start_time": "06:00", "end_time": "14:00"}, kind="json"),
    ]),
    "export_employee_shifts_ics": Recipe([Req("GET", "/employees/{employee_id}/export.ics")]),
    "approve_leave_request_page": Recipe([Req("POST", "/leave-requests/{request_id}/approve", {"decision_note": "ok"})]),
    "reject_leave_request_page": Recipe([Req("POST", "/leave-requests/{request_id}/reject", {"decision_note": "nie"})]),
    "delete_shift_page": Recipe([Req("POST", "/shifts/delete/{shift_id}")]),
    "edit_shift_page": Recipe([
        Req("GET", "/shifts/edit/{shift_id}"),
        Req("POST", "/shifts/edit/{shift_id}", {**SHIFT_FORM, "shift_date": "{far_date}"}),
    ]),
    "apply_shift_template_page": Recipe([
        Req("GET", "/shifts/templates/{template_id}/apply", legit_status=302),   # GET len presmeruje na zoznam
        Req("POST", "/shifts/templates/{template_id}/apply", {"action": "apply", "target_date": "{target_monday}"}),
    ], legit_changes_state=True),
    "delete_shift_template_page": Recipe([Req("POST", "/shifts/templates/{template_id}/delete")]),
    "approve_swap_page": Recipe([Req("POST", "/swap-requests/{swap_id}/approve", {"decision_note": "ok"})]),
    "reject_swap_page": Recipe([Req("POST", "/swap-requests/{swap_id}/reject", {"decision_note": "nie"})]),
    # -------- zamestnanec (len vlastné objekty) --------
    "delete_availability_rule_page": Recipe([Req("POST", "/my-availability/{rule_id}/delete")], legit="e2"),
    "cancel_my_request_page": Recipe([Req("POST", "/my-requests/{request_id}/cancel")], legit="e2"),
    "cancel_swap_page": Recipe([Req("POST", "/my-swaps/{swap_id_peer}/cancel")], legit="e2"),
    "respond_swap_page": Recipe([Req("POST", "/my-swaps/{swap_id_peer}/respond", {"action": "accept"})], legit="e3"),
    # -------- admin --------
    "edit_branch_page": Recipe([
        Req("GET", "/branches/edit/{branch_id}"),
        Req("POST", "/branches/edit/{branch_id}", {"name": "Premenovana-{key}"}),
    ], legit="admin"),
    "toggle_branch_page": Recipe([Req("POST", "/branches/toggle/{branch_id}")], legit="admin"),
    "edit_department_page": Recipe([
        Req("GET", "/departments/edit/{department_id}"),
        Req("POST", "/departments/edit/{department_id}", {"name": "Odd-premenovane-{key}", "min_staff": "2"}),
    ], legit="admin"),
    "toggle_department_page": Recipe([Req("POST", "/departments/toggle/{department_id}")], legit="admin"),
    "create_employee_credentials_page": Recipe([
        Req("POST", "/employees/{employee_no_account_id}/credentials/create",
            {"username": "novy_ucet_{key}", "password": "heslo12345"}),
    ], legit="admin"),
    "delete_employee_credentials_page": Recipe([Req("POST", "/employees/{employee_id}/credentials/delete")], legit="admin"),
    "update_employee_credentials_page": Recipe([
        Req("POST", "/employees/{employee_id}/credentials/update", {"username": "premenovany_{key}", "password": ""}),
    ], legit="admin"),
    "add_employee_department_page": Recipe([
        # e4 ešte nemá priradené oddelenie (e1 áno - išlo by o duplicitu)
        Req("GET", "/employees/{employee_no_account_id}/departments/add"),
        Req("POST", "/employees/{employee_no_account_id}/departments/add", {"department_id": "{department_id}", "weekly_hours": "10"}),
    ], legit="admin"),
    "delete_employee_department_page": Recipe([
        Req("POST", "/employees/{employee_id}/departments/delete/{assignment_id}"),
    ], legit="admin"),
    "edit_employee_department_page": Recipe([
        Req("GET", "/employees/{employee_id}/departments/edit/{assignment_id}"),
        Req("POST", "/employees/{employee_id}/departments/edit/{assignment_id}",
            {"department_id": "{department_id}", "weekly_hours": "12"}),
    ], legit="admin"),
    "update_employee_manager_page": Recipe([
        Req("POST", "/employees/{employee_id}/manager", {"manager_enabled": "1", "branch_ids": "{branch_id}"}),
    ], legit="admin"),
    "edit_employee_page": Recipe([
        Req("GET", "/employees/edit/{employee_id}"),
        Req("POST", "/employees/edit/{employee_id}", {
            "first_name": "Zmenene", "last_name": "Meno{key}", "position": "Iny", "weekly_hours": "45",
        }),
    ], legit="admin"),
    "toggle_employee_page": Recipe([Req("POST", "/employees/toggle/{employee_id}")], legit="admin"),
    "delete_user_page": Recipe([Req("POST", "/users/delete/{user_id}")], legit="admin"),
    "edit_user_page": Recipe([
        Req("GET", "/users/edit/{user_id}"),
        Req("POST", "/users/edit/{user_id}", {"username": "mgr_premenovany_{key}", "branch_ids": "{branch_id}"}),
    ], legit="admin"),
}

ATTACKERS = [
    # (používateľ, pobočka, ktorej objekty sa napádajú, popis)
    ("mgr_a", "b", "manažér A -> objekty B"),
    ("mgr_b", "a", "manažér B -> objekty A"),
    ("emp_a1", "b", "zamestnanec A -> objekty B"),
    ("emp_b1", "a", "zamestnanec B -> objekty A"),
    ("emp_a1", "a", "zamestnanec A -> objekty kolegov z A"),
    ("emp_b1", "b", "zamestnanec B -> objekty kolegov z B"),
]

# Zamestnanec smie na objektoch vlastnej pobočky vykonať len "vlastné" trasy
# - tie, ktoré v nich patria jemu. Pri objektoch kolegov (e2/e3) teda platí
# rovnaký zákaz ako pri cudzej pobočke. Pre e1 sú vlastné len objekty e1.
OWN_OBJECT_RECIPES_FOR_E1 = set()      # e1 nevlastní žiadny z objektov v matici (patria e2/e3)


# ---------------------------------------------------------------------
# Matica útokov
# ---------------------------------------------------------------------

def attack_cases():
    for attacker, target, label in ATTACKERS:
        for name, recipe in RECIPES.items():
            for index, request in enumerate(recipe.requests):
                yield pytest.param(
                    attacker, target, name, index,
                    id=f"{label}|{name}|{request.method}-{index}",
                )


@pytest.mark.parametrize("attacker, target, name, index", list(attack_cases()))
def test_foreign_objects_are_untouchable_and_invisible(attack_app, attacker, target, name, index):
    recipe = RECIPES[name]
    request = recipe.requests[index]
    ids = attack_app.worlds[target]
    other = "a" if target == "b" else "b"

    client = login(attack_app, attacker)
    token = token_for(client, attacker)

    before = snapshot(attack_app)
    response = send(client, request, ids, token)
    after = snapshot(attack_app)

    assert response.status_code != 400, "požiadavka zlyhala na CSRF/neplatných údajoch, test by nič nedokazoval"
    assert response.status_code in (302, 403, 404), (
        f"{attacker}: {request.method} {expand(request.url, ids)} -> {response.status_code}"
    )

    changed = {t for t in before if before[t] != after[t]}
    assert not changed, f"{attacker} zmenil cudzie dáta v tabuľkách {sorted(changed)}"

    body = response.get_data(as_text=True)
    assert ids["token"] not in body, f"odpoveď prezradila údaje objektu {target.upper()}"
    assert attack_app.worlds[other]["token"] not in body or other == target


# ---------------------------------------------------------------------
# Pozitívne kontroly: oprávnený používateľ prejde
# ---------------------------------------------------------------------

def legit_cases():
    for name, recipe in RECIPES.items():
        for index, request in enumerate(recipe.requests):
            yield pytest.param(name, index, id=f"{name}|{request.method}-{index}")


@pytest.mark.parametrize("name, index", list(legit_cases()))
def test_the_legitimate_user_can_do_the_same_on_own_objects(fresh_app, name, index):
    recipe = RECIPES[name]
    request = recipe.requests[index]
    ids = fresh_app.worlds["a"]

    username = {
        "mgr": "mgr_a", "admin": "admin", "e2": "emp_a2", "e3": "emp_a3",
    }[recipe.legit]

    client = login(fresh_app, username)
    token = token_for(client, username)

    before = snapshot(fresh_app)
    response = send(client, request, ids, token)
    after = snapshot(fresh_app)

    assert response.status_code in (200, 302), f"{username}: {request.url} -> {response.status_code}"

    if request.method == "GET":
        assert response.status_code == request.legit_status
    else:
        assert before != after, f"{username}: {request.url} nič nezmenilo (zlá požiadavka?)"


# ---------------------------------------------------------------------
# Pokrytie: každá trasa s ID musí byť v matici
# ---------------------------------------------------------------------

def test_every_route_with_an_object_id_is_in_the_matrix(attack_app):
    routes_with_ids = {
        rule.endpoint.split(".", 1)[1]
        for rule in attack_app.url_map.iter_rules()
        if rule.endpoint.startswith("main.") and rule.arguments
    }

    missing = routes_with_ids - set(RECIPES)
    stale = set(RECIPES) - routes_with_ids

    assert not missing, f"trasy s ID bez záznamu v matici izolácie: {sorted(missing)}"
    assert not stale, f"záznamy v matici bez existujúcej trasy: {sorted(stale)}"


def test_matrix_urls_match_the_real_routes(attack_app):
    """Každý riadok matice musí zodpovedať skutočnej trase (preklep v URL
    by inak spravil test prázdnym: 404 by "prešiel" ako zamietnutie)."""

    adapter = attack_app.url_map.bind("localhost")
    ids = attack_app.worlds["a"]

    for name, recipe in RECIPES.items():
        for request in recipe.requests:
            method = "POST" if request.kind == "json" else request.method
            endpoint, _ = adapter.match(expand(request.url, ids), method=method)

            assert endpoint == f"main.{name}", (name, request.url)


# ---------------------------------------------------------------------
# Filtre v URL: výpisy a exporty nesmú prezradiť údaje cudzej pobočky
# ---------------------------------------------------------------------

def response_text(response):
    """Čitateľný text odpovede (HTML/CSV/ICS/JSON; XLSX sa rozbalí)."""

    mimetype = response.mimetype

    if "spreadsheetml" in mimetype:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(response.data))

        return " ".join(
            str(cell)
            for sheet in workbook
            for row in sheet.iter_rows(values_only=True)
            for cell in row
            if cell is not None
        )

    if "pdf" in mimetype:
        return ""                       # text v PDF je komprimovaný; kontroluje sa len stav

    return response.get_data(as_text=True)


def filter_variants(foreign):
    return [
        "",
        f"branch_id={foreign['branch_id']}",
        f"employee_id={foreign['employee_id']}",
        f"department_id={foreign['department_id']}",
        f"branch_id={foreign['branch_id']}&employee_id={foreign['employee_id']}"
        f"&department_id={foreign['department_id']}",
    ]


def with_query(url, extra):
    if not extra:
        return url

    return url + ("&" if "?" in url else "?") + extra


PERIOD = f"year={MON.year}&month={MON.month}"
RANGE = f"start={iso(-7)}&end={iso(30)}"

MANAGER_URLS = [
    "/", "/employees", "/departments", "/shifts", f"/calendar?{PERIOD}", "/absences",
    "/availability", "/leave-requests?status=all", "/swap-requests?status=all", "/audit",
    f"/reports/hours?{PERIOD}", "/shifts/templates", "/shifts/publish", "/shifts/copy-week",
    "/shifts/export.xlsx", "/shifts/export.ics", "/shifts/export.pdf",
    f"/calendar/export.xlsx?{PERIOD}", f"/calendar/export.ics?{PERIOD}", f"/calendar/export.pdf?{PERIOD}",
    "/audit/export.csv", f"/reports/hours/export.xlsx?{PERIOD}", f"/reports/hours/export.csv?{PERIOD}",
    f"/api/shifts?{RANGE}",
]

EMPLOYEE_URLS = [
    "/my-schedule", "/my-requests", "/my-swaps", "/my-availability",
    "/my-schedule/export.ics", "/my-schedule/export.xlsx", "/my-schedule/export.pdf",
    f"/calendar?{PERIOD}", f"/calendar/export.xlsx?{PERIOD}", f"/calendar/export.ics?{PERIOD}",
    f"/api/shifts?{RANGE}",
]


@pytest.mark.parametrize(
    "attacker, own, foreign_key",
    [("mgr_a", "a", "b"), ("mgr_b", "b", "a"), ("emp_a1", "a", "b"), ("emp_b1", "b", "a")],
)
def test_listings_and_exports_ignore_foreign_filters(attack_app, attacker, own, foreign_key):
    foreign = attack_app.worlds[foreign_key]
    urls = MANAGER_URLS if attacker.startswith("mgr_") else EMPLOYEE_URLS
    client = login(attack_app, attacker)

    own_token_seen = False

    for base in urls:
        for extra in filter_variants(foreign):
            url = with_query(base, extra)
            response = client.get(url)

            assert response.status_code in (200, 302, 403, 404), (attacker, url, response.status_code)

            text = response_text(response)
            assert foreign["token"] not in text, f"{attacker}: {url} prezradilo údaje pobočky {foreign_key.upper()}"

            own_token_seen |= attack_app.worlds[own]["token"] in text

    # Kontrola, že test nie je prázdny: vlastné údaje sa v odpovediach vyskytujú.
    assert own_token_seen


# ---------------------------------------------------------------------
# ID cudzích objektov v TELE požiadavky (vytváranie/úprava)
# ---------------------------------------------------------------------

def post_as(app, username, url, data, kind="form"):
    client = login(app, username)
    token = token_for(client, username)

    if kind == "json":
        return client.post(url, json=data, headers={"X-CSRFToken": token})

    return client.post(url, data={"csrf_token": token, **data})


def assert_nothing_changed(app, username, url, data, foreign, expected=(200, 302, 403, 404)):
    before = snapshot(app)
    response = post_as(app, username, url, data)
    after = snapshot(app)

    assert response.status_code != 400, (url, "neplatný CSRF")
    assert response.status_code in expected, (username, url, response.status_code)
    assert before == after, f"{username}: {url} zmenilo dáta"
    assert foreign["token"] not in response.get_data(as_text=True)


def test_manager_cannot_use_foreign_ids_in_request_bodies(attack_app):
    own, foreign = attack_app.worlds["a"], attack_app.worlds["b"]

    shift_form = {
        "employee_id": str(foreign["e1"]), "department_id": "", "shift_date": iso(5),
        "start_time": "06:00", "end_time": "14:00", "shift_type": "Ranná",
    }

    # Nová smena cudziemu zamestnancovi.
    assert_nothing_changed(attack_app, "mgr_a", "/shifts/add", shift_form, foreign)

    # Smena vlastnému zamestnancovi, ale s oddelením cudzej pobočky.
    assert_nothing_changed(
        attack_app, "mgr_a", "/shifts/add",
        {**shift_form, "employee_id": str(own["e1"]), "department_id": str(foreign["department_id"])}, foreign,
    )

    # Vlastnú smenu prepísať na cudzieho zamestnanca.
    assert_nothing_changed(
        attack_app, "mgr_a", f"/shifts/edit/{own['shift_id']}",
        {**shift_form, "shift_date": own["monday"]}, foreign,
    )

    # Absencia cudziemu zamestnancovi.
    assert_nothing_changed(
        attack_app, "mgr_a", "/absences/add",
        {"employee_id": str(foreign["e1"]), "absence_type": "PN", "start_date": iso(40), "end_date": iso(41), "note": ""},
        foreign,
    )

    # Vlastnú absenciu prepísať na cudzieho zamestnanca.
    assert_nothing_changed(
        attack_app, "mgr_a", f"/absences/edit/{own['absence_id']}",
        {"employee_id": str(foreign["e1"]), "absence_type": "PN", "start_date": iso(20), "end_date": iso(21), "note": ""},
        foreign,
    )

    # Šablóna, kopírovanie a zverejnenie týždňa pre cudziu pobočku.
    assert_nothing_changed(
        attack_app, "mgr_a", "/shifts/templates/save",
        {"branch_id": str(foreign["branch_id"]), "source_date": foreign["monday"], "name": "Hack"}, foreign,
    )

    for action in ("preview", "copy"):
        assert_nothing_changed(
            attack_app, "mgr_a", "/shifts/copy-week",
            {"action": action, "branch_id": str(foreign["branch_id"]),
             "source_date": foreign["monday"], "target_date": foreign["target_monday"]}, foreign,
        )

    for action in ("publish", "unpublish"):
        assert_nothing_changed(
            attack_app, "mgr_a", "/shifts/publish",
            {"action": action, "branch_id": str(foreign["branch_id"]), "week_date": foreign["monday"]}, foreign,
        )


def test_employee_cannot_use_foreign_ids_in_request_bodies(attack_app):
    own, foreign = attack_app.worlds["a"], attack_app.worlds["b"]

    # Návrh výmeny: kolega z cudzej pobočky / cudzia smena / smena kolegu.
    for data in (
        {"shift_id": str(own["shift_id"]), "colleague_id": str(foreign["e1"])},
        {"shift_id": str(foreign["shift_id"]), "colleague_id": str(own["e2"])},
        {"shift_id": str(own["shift_e2"]), "colleague_id": str(own["e3"])},          # smena kolegu e2
    ):
        assert_nothing_changed(attack_app, "emp_a1", "/my-swaps", data, foreign)

    # Odpoveď na výmenu, ktorej nie je stranou (v cudzej pobočke aj v vlastnej).
    for target in (foreign, own):
        assert_nothing_changed(
            attack_app, "emp_a1", f"/my-swaps/{target['swap_id_peer']}/respond",
            {"action": "accept", "counter_shift_id": str(target["shift_e3"])}, foreign,
        )

    # Protihodnota výmeny: kolega (e3) je skutočná strana výmeny typu "za inú
    # smenu", ale ako protihodnotu podsúva smenu cudzej pobočky / smenu iného
    # kolegu. Musí sa odmietnuť a nič sa nesmie zmeniť.
    from app.extensions import db
    from app.services.shift_service import add_shift
    from app.services.shift_swap_service import create_swap

    with attack_app.app_context():
        extra_shift = add_shift(own["e2"], iso(6), "06:00", "14:00", "Ranná", published=True)
        exchange_id, error = create_swap(own["e2"], own["e3"], extra_shift, wants_swap=True)
        assert error is None, error
        db.session.remove()

    for counter in (foreign["shift_e3"], own["shift_e2"], own["shift_id"]):    # cudzia, ponúkaná, cudzia v A
        assert_nothing_changed(
            attack_app, "emp_a3", f"/my-swaps/{exchange_id}/respond",
            {"action": "accept", "counter_shift_id": str(counter)}, foreign,
        )


def test_a_manager_of_two_branches_still_cannot_reach_a_third(tmp_path):
    """Manažér s pridelenými pobočkami A a B nevidí ani nemení pobočku C."""

    from app.services.auth_service import create_manager

    app = build_world(tmp_path)

    with app.app_context():
        app.worlds["c"] = build_branch("c", "CCCTOKEN")
        create_manager("mgr_ab", PASSWORD, [app.worlds["a"]["branch_id"], app.worlds["b"]["branch_id"]])

    foreign = app.worlds["c"]
    client = login(app, "mgr_ab")
    token = token_for(client, "mgr_ab")

    for name in ("delete_shift_page", "delete_absence_page", "approve_leave_request_page",
                 "delete_shift_template_page", "approve_swap_page", "api_move_shift"):
        for request in RECIPES[name].requests:
            before = snapshot(app)
            response = send(client, request, foreign, token)

            assert response.status_code in (302, 403, 404), (name, response.status_code)
            assert before == snapshot(app), name
            assert foreign["token"] not in response.get_data(as_text=True)

    for base in MANAGER_URLS:
        for extra in filter_variants(foreign):
            response = client.get(with_query(base, extra))
            assert foreign["token"] not in response_text(response), (base, extra)

    # Vlastné pobočky (A aj B) vidí.
    assert "AAATOKEN" in client.get("/employees").get_data(as_text=True)
    assert "BBBTOKEN" in client.get("/employees").get_data(as_text=True)
