"""Testy kopírovania týždňa a šablón smien.

Scenár (pobočka A "Košice"), zdrojový týždeň 2026-09-21 .. 2026-09-27:
  - Anna (40 h, oddelenie Výroba): po 06-14, ut 06-14, st 14-22
  - Cyril (fond 16 h, bez oddelenia): po, ut, st vždy 06-14 (3x8 = 24 h)
  - Dana: štvrtok 06-14, ale je neaktívna
pobočka B "Prešov": Boris, pondelok 06-14.
Cieľový týždeň 2026-09-28 .. 2026-10-04: Anna má v utorok absenciu a v
pondelok už rovnakú smenu.
"""

import re

import pytest


def _csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


@pytest.fixture
def plan_world(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "plan.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    ids = {}

    with app.app_context():
        db.create_all()

        from app.services.absence_service import add_absence
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
        from app.services.employee_service import (
            add_employee,
            set_employee_active,
        )
        from app.services.shift_service import add_shift

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")
        ids["dep_a"] = add_department("Výroba", branch_id=ids["a"])

        ids["anna"] = add_employee("Anna", "Prvá", "Op", 40, ids["a"])
        ids["cyril"] = add_employee("Cyril", "Tretí", "Op", 16, ids["a"])
        ids["dana"] = add_employee("Dana", "Štvrtá", "Op", 40, ids["a"])
        ids["boris"] = add_employee("Boris", "Druhý", "Op", 40, ids["b"])

        add_employee_department(ids["anna"], ids["dep_a"], 40)

        def shift(emp, day, start, end, dep=None):
            return add_shift(
                emp, f"2026-09-{day}", start, end, "Ranná", department_id=dep
            )

        shift(ids["anna"], 21, "06:00", "14:00", ids["dep_a"])
        shift(ids["anna"], 22, "06:00", "14:00", ids["dep_a"])
        shift(ids["anna"], 23, "14:00", "22:00", ids["dep_a"])
        shift(ids["cyril"], 21, "06:00", "14:00")
        shift(ids["cyril"], 22, "06:00", "14:00")
        shift(ids["cyril"], 23, "06:00", "14:00")
        shift(ids["dana"], 24, "06:00", "14:00")
        shift(ids["boris"], 21, "06:00", "14:00")

        set_employee_active(ids["dana"], 0)

        # Cieľový týždeň: absencia v utorok a už existujúca pondelňajšia smena.
        add_absence(ids["anna"], "Dovolenka", "2026-09-29", "2026-09-29")
        shift(ids["anna"], 28, "06:00", "14:00", ids["dep_a"])

        create_admin("admin", "adminpass1")
        create_manager("mgr_a", "managerpass1", [ids["a"]])
        create_employee_user(ids["anna"], "emp_a", "emppass12")

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


def post(client, page, url, data):
    token = _csrf(client.get(page).get_data(as_text=True))
    return client.post(url, data={"csrf_token": token, **data})


def shifts_in_week(app, monday_date, days=7):
    from datetime import date, timedelta

    from app.extensions import db
    from app.services.shift_service import get_shifts

    start = date.fromisoformat(monday_date)
    wanted = {(start + timedelta(days=d)).isoformat() for d in range(days)}

    with app.app_context():
        return sorted(
            (row[3], row[7], row[4], row[5])
            for row in get_shifts()
            if row[3] in wanted
        )


COPY = {
    "source_date": "2026-09-21",
    "target_date": "2026-09-28",
}


def copy_week(client, app, action="copy", **overrides):
    data = {"branch_id": str(app.test_ids["a"]), "action": action, **COPY}
    data.update(overrides)

    return post(client, "/shifts/copy-week", "/shifts/copy-week", data)


# ---------------------------------------------------------------------
# Kopírovanie týždňa
# ---------------------------------------------------------------------

def test_preview_shows_plan_but_writes_nothing(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    before = shifts_in_week(plan_world, "2026-09-28")

    response = copy_week(admin, plan_world, action="preview")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Náhľad" in html
    assert "zatiaľ sa nič neuložilo" in html
    assert shifts_in_week(plan_world, "2026-09-28") == before


def test_copy_creates_valid_shifts_and_skips_with_reasons(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    ids = plan_world.test_ids

    response = copy_week(admin, plan_world)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Hotovo" in html

    created = shifts_in_week(plan_world, "2026-09-28")

    # Anna: po (už existovala - len jedna), ut (absencia) -> preskočené,
    # st 14-22 -> nová. Cyril: po, ut OK, st (3. smena = 24 h > fond 16 h).
    assert created == sorted([
        ("2026-09-28", ids["anna"], "06:00", "14:00"),   # pôvodná
        ("2026-09-30", ids["anna"], "14:00", "22:00"),
        ("2026-09-28", ids["cyril"], "06:00", "14:00"),
        ("2026-09-29", ids["cyril"], "06:00", "14:00"),
    ])

    assert "neprítomnosť" in html
    assert "pracovný fond" in html
    assert "neaktívny" in html
    assert "už v tomto týždni" in html

    # Smena z pobočky B sa nekopírovala.
    assert all(row[1] != ids["boris"] for row in created)


def test_weekly_hours_accumulate_across_copied_shifts(plan_world):
    """Fond 16 h: prvé dve smeny prejdú, tretia (24 h) nie - pravidlo sa
    počíta aj medzi smenami z toho istého kopírovania."""

    from datetime import date

    from app.services.week_plan_service import entries_from_week, run_plan

    ids = plan_world.test_ids

    with plan_world.app_context():
        entries = [
            e for e in entries_from_week(date(2026, 9, 21), ids["a"])
            if e.employee_id == ids["cyril"]
        ]
        rows = run_plan(entries, date(2026, 10, 5), commit=False)

    assert [row.status for row in rows] == ["create", "create", "skip"]
    assert "pracovný fond" in rows[2].reasons[0]

    # Náhľad nič nezapísal.
    assert shifts_in_week(plan_world, "2026-10-05") == []


def test_copy_twice_does_not_duplicate(plan_world):
    admin = login(plan_world, "admin", "adminpass1")

    copy_week(admin, plan_world)
    first = shifts_in_week(plan_world, "2026-09-28")

    copy_week(admin, plan_world)

    assert shifts_in_week(plan_world, "2026-09-28") == first


def test_any_day_of_the_week_is_accepted(plan_world):
    admin = login(plan_world, "admin", "adminpass1")

    copy_week(
        admin, plan_world,
        source_date="2026-09-23",   # streda
        target_date="2026-10-02",   # piatok
    )

    assert shifts_in_week(plan_world, "2026-09-28") == shifts_in_week(
        plan_world, "2026-09-28"
    )
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 4


def test_same_source_and_target_week_is_rejected(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    before = shifts_in_week(plan_world, "2026-09-21")

    response = copy_week(admin, plan_world, target_date="2026-09-24")

    assert "rovnaké" in response.get_data(as_text=True)
    assert shifts_in_week(plan_world, "2026-09-21") == before


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"source_date": "nie-datum"}, "platný dátum"),
        ({"target_date": ""}, "platný dátum"),
        ({"branch_id": ""}, "Vyber pobočku"),
        ({"branch_id": "99999"}, "Vyber pobočku"),
        ({"source_date": "2030-01-07"}, "žiadne smeny"),
    ],
)
def test_invalid_input_is_rejected_without_changes(plan_world, overrides, message):
    admin = login(plan_world, "admin", "adminpass1")
    before = shifts_in_week(plan_world, "2026-09-28")

    response = copy_week(admin, plan_world, **overrides)

    assert message in response.get_data(as_text=True)
    assert shifts_in_week(plan_world, "2026-09-28") == before


def test_manager_can_only_copy_in_own_branch(plan_world):
    manager = login(plan_world, "mgr_a", "managerpass1")
    ids = plan_world.test_ids

    # Vlastná pobočka funguje.
    copy_week(manager, plan_world)
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 4

    # Cudzia pobočka B sa nepustí.
    response = copy_week(
        manager, plan_world, branch_id=str(ids["b"]), target_date="2026-10-12"
    )

    assert "Vyber pobočku" in response.get_data(as_text=True)
    assert shifts_in_week(plan_world, "2026-10-12") == []


def test_employee_account_cannot_use_planning_pages(plan_world):
    employee = login(plan_world, "emp_a", "emppass12")

    for url in ("/shifts/copy-week", "/shifts/templates"):
        assert employee.get(url).status_code == 403

    token = _csrf(employee.get("/my-schedule").get_data(as_text=True))
    response = employee.post(
        "/shifts/copy-week",
        data={"csrf_token": token, "branch_id": str(plan_world.test_ids["a"]), **COPY},
    )
    assert response.status_code == 403
    assert shifts_in_week(plan_world, "2026-09-28") == [
        ("2026-09-28", plan_world.test_ids["anna"], "06:00", "14:00")
    ]


def test_employee_with_manager_function_can_copy_in_assigned_branch(plan_world):
    from app.services.auth_service import set_manager_function

    ids = plan_world.test_ids

    with plan_world.app_context():
        set_manager_function(ids["anna"], True, [ids["a"]])

    worker_manager = login(plan_world, "emp_a", "emppass12")

    assert worker_manager.get("/shifts/copy-week").status_code == 200

    copy_week(worker_manager, plan_world)
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 4


# ---------------------------------------------------------------------
# Šablóny
# ---------------------------------------------------------------------

def save_template(client, app, name="Štandard", **overrides):
    data = {
        "branch_id": str(app.test_ids["a"]),
        "source_date": "2026-09-21",
        "name": name,
    }
    data.update(overrides)

    return post(client, "/shifts/templates", "/shifts/templates/save", data)


def only_template_id(app, name):
    from app.services.week_plan_service import get_templates

    with app.app_context():
        found = [t for t in get_templates() if t[1] == name]

    assert len(found) == 1
    return found[0][0]


def test_save_template_from_week_and_list_it(plan_world):
    from app.services.week_plan_service import get_templates

    admin = login(plan_world, "admin", "adminpass1")

    response = save_template(admin, plan_world)
    assert response.status_code == 302

    with plan_world.app_context():
        templates = get_templates()

    # 3 (Anna) + 3 (Cyril) + 1 (Dana) smien z pobočky A.
    assert [(t[1], t[4]) for t in templates] == [("Štandard", 7)]

    html = admin.get("/shifts/templates").get_data(as_text=True)
    assert "Štandard" in html and "Košice" in html


def test_template_name_must_be_unique_per_branch(plan_world):
    from app.services.week_plan_service import get_templates

    admin = login(plan_world, "admin", "adminpass1")

    save_template(admin, plan_world, name="Štandard")
    response = save_template(admin, plan_world, name="štandard")
    assert response.status_code == 302

    html = admin.get("/shifts/templates").get_data(as_text=True)
    assert "už v tejto pobočke existuje" in html

    with plan_world.app_context():
        assert len(get_templates()) == 1


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"name": "   "}, "Zadaj názov"),
        ({"source_date": "2030-01-07"}, "žiadne smeny"),
        ({"source_date": "x"}, "platný dátum"),
        ({"branch_id": "99999"}, "Vyber pobočku"),
    ],
)
def test_save_template_validation(plan_world, overrides, message):
    from app.services.week_plan_service import get_templates

    admin = login(plan_world, "admin", "adminpass1")

    save_template(admin, plan_world, **overrides)

    assert message in admin.get("/shifts/templates").get_data(as_text=True)

    with plan_world.app_context():
        assert get_templates() == []


def test_apply_template_previews_then_creates(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    save_template(admin, plan_world)
    template_id = only_template_id(plan_world, "Štandard")
    url = f"/shifts/templates/{template_id}/apply"

    preview = post(
        admin, "/shifts/templates", url,
        {"action": "preview", "target_date": "2026-09-28"},
    )
    assert "zatiaľ sa nič neuložilo" in preview.get_data(as_text=True)
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 1

    applied = post(
        admin, "/shifts/templates", url,
        {"action": "apply", "target_date": "2026-09-30"},
    )
    html = applied.get_data(as_text=True)
    assert "Hotovo" in html

    # Cieľ 28.9.-4.10.: rovnaký výsledok ako pri kopírovaní týždňa.
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 4


def test_template_applies_to_a_clean_week_fully_when_rules_allow(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    save_template(admin, plan_world)
    template_id = only_template_id(plan_world, "Štandard")

    post(
        admin, "/shifts/templates", f"/shifts/templates/{template_id}/apply",
        {"action": "apply", "target_date": "2026-10-12"},
    )

    created = shifts_in_week(plan_world, "2026-10-12")
    ids = plan_world.test_ids

    # Bez absencie a duplicity: Anna 3, Cyril 2 (fond 16 h); Dana neaktívna.
    assert len([r for r in created if r[1] == ids["anna"]]) == 3
    assert len([r for r in created if r[1] == ids["cyril"]]) == 2
    assert all(r[1] != ids["dana"] for r in created)


def test_delete_template(plan_world):
    from app.services.week_plan_service import get_templates

    admin = login(plan_world, "admin", "adminpass1")
    save_template(admin, plan_world)
    template_id = only_template_id(plan_world, "Štandard")

    post(admin, "/shifts/templates", f"/shifts/templates/{template_id}/delete", {})

    with plan_world.app_context():
        assert get_templates() == []

    # Aj záznamy šablóny zmizli (kaskáda).
    from app.extensions import db
    from app.orm_models import ShiftTemplateEntry

    with plan_world.app_context():
        assert db.session.query(ShiftTemplateEntry).count() == 0


def test_templates_are_scoped_by_branch(plan_world):
    ids = plan_world.test_ids

    admin = login(plan_world, "admin", "adminpass1")
    save_template(admin, plan_world, name="Košice týždeň")
    save_template(
        admin, plan_world, name="Prešov týždeň", branch_id=str(ids["b"])
    )
    foreign_id = only_template_id(plan_world, "Prešov týždeň")

    manager = login(plan_world, "mgr_a", "managerpass1")

    html = manager.get("/shifts/templates").get_data(as_text=True)
    assert "Košice týždeň" in html
    assert "Prešov týždeň" not in html

    # Cudziu šablónu nevie použiť ani zmazať.
    token = _csrf(html)
    for url, data in (
        (f"/shifts/templates/{foreign_id}/apply",
         {"action": "apply", "target_date": "2026-10-12"}),
        (f"/shifts/templates/{foreign_id}/delete", {}),
    ):
        response = manager.post(url, data={"csrf_token": token, **data})
        assert response.status_code == 404, url

    assert shifts_in_week(plan_world, "2026-10-12") == []

    # Do cudzej pobočky nevie ani uložiť šablónu.
    save_template(manager, plan_world, name="Hack", branch_id=str(ids["b"]))
    assert "Hack" not in admin.get("/shifts/templates").get_data(as_text=True)


def test_apply_template_with_invalid_date_changes_nothing(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    save_template(admin, plan_world)
    template_id = only_template_id(plan_world, "Štandard")

    response = post(
        admin, "/shifts/templates", f"/shifts/templates/{template_id}/apply",
        {"action": "apply", "target_date": "zle"},
    )

    assert response.status_code == 302
    assert len(shifts_in_week(plan_world, "2026-09-28")) == 1


def test_planning_pages_render(plan_world):
    admin = login(plan_world, "admin", "adminpass1")
    manager = login(plan_world, "mgr_a", "managerpass1")

    for client in (admin, manager):
        for url in ("/shifts/copy-week", "/shifts/templates", "/shifts"):
            assert client.get(url).status_code == 200, url

    html = manager.get("/shifts").get_data(as_text=True)
    assert "/shifts/copy-week" in html and "/shifts/templates" in html

    # Zamestnanec s účtom bez funkcie manažéra stránky nevidí.
    assert "/shifts/copy-week" not in login(
        plan_world, "emp_a", "emppass12"
    ).get("/my-schedule").get_data(as_text=True)


# ---------------------------------------------------------------------
# Migrácia (nové tabuľky)
# ---------------------------------------------------------------------

def test_template_tables_migration_roundtrip(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "mig.db")

    def tables():
        return set(sa.inspect(db.engine).get_table_names())

    with app.app_context():
        upgrade()
        assert {"shift_templates", "shift_template_entries"} <= tables()

        downgrade(revision="b7c1d2e3f405")
        assert "shift_templates" not in tables()
        assert "branches" in tables()

        upgrade()
        assert {"shift_templates", "shift_template_entries"} <= tables()


# ---------------------------------------------------------------------
# Názvy s diakritikou (SQLite lower() ich nerozlišuje)
# ---------------------------------------------------------------------

def test_names_are_compared_case_insensitively_with_diacritics(plan_world):
    from app.services.branch_service import add_branch, branch_name_exists
    from app.services.department_service import (
        add_department,
        department_name_exists,
    )

    ids = plan_world.test_ids

    with plan_world.app_context():
        assert branch_name_exists("KOŠICE")
        assert branch_name_exists("  košice ")
        assert add_branch("košice") is None
        assert add_branch("Šurany") is not None
        assert add_branch("šurany") is None

        assert department_name_exists("VÝROBA", ids["a"])
        assert add_department("výroba", branch_id=ids["a"]) is None
        # V inej pobočke je rovnaký názov v poriadku.
        assert add_department("výroba", branch_id=ids["b"]) is not None
