"""Testy výmeny smien medzi kolegami.

Pobočka A "Košice": Anna, Cyril (obaja s účtom), Dana (neaktívna, s
účtom), Eva (bez účtu). Pobočka B "Prešov": Boris (s účtom).
Všetky smeny sú v tom istom budúcom týždni (D1 = pondelok ... D4):
  Anna:  sa1 D1 06-14, sa2 D2 06-14, sa_draft D3 06-14 (koncept),
         sa_past (minulosť), sa_dept D4 06-14 s oddelením Výroba
  Cyril: sc1 D2 14-22, sc2 D3 06-14, sc3 D5 06-14 (Anna ten deň nemá nič)
  Boris: sb1 D1 06-14
"""

import re
from datetime import date, timedelta

import pytest


def _csrf(html):
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa nenašiel")


TODAY = date.today()
MON = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)   # budúci pondelok
D1, D2, D3, D4, D5 = (MON + timedelta(days=n) for n in range(5))
PAST = TODAY - timedelta(days=3)


@pytest.fixture
def sw(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "swaps.db")
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
        from app.services.employee_department_service import add_employee_department
        from app.services.employee_service import add_employee, set_employee_active
        from app.services.shift_service import add_shift

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")
        ids["dep"] = add_department("Výroba", branch_id=ids["a"])

        ids["anna"] = add_employee("Anna", "Prvá", "Op", 40, ids["a"])
        ids["cyril"] = add_employee("Cyril", "Tretí", "Op", 40, ids["a"])
        ids["dana"] = add_employee("Dana", "Štvrtá", "Op", 40, ids["a"])
        ids["eva"] = add_employee("Eva", "Piata", "Op", 40, ids["a"])
        ids["boris"] = add_employee("Boris", "Druhý", "Op", 40, ids["b"])
        add_employee_department(ids["anna"], ids["dep"], 40)

        def shift(emp, day, start, end, published=True, department_id=None):
            return add_shift(
                emp, day.isoformat(), start, end, "Ranná",
                department_id=department_id, published=published,
            )

        ids["sa1"] = shift(ids["anna"], D1, "06:00", "14:00")
        ids["sa2"] = shift(ids["anna"], D2, "06:00", "14:00")
        ids["sa_draft"] = shift(ids["anna"], D3, "06:00", "14:00", published=False)
        ids["sa_past"] = shift(ids["anna"], PAST, "06:00", "14:00")
        ids["sa_dept"] = shift(ids["anna"], D4, "06:00", "14:00", department_id=ids["dep"])
        ids["sc1"] = shift(ids["cyril"], D2, "14:00", "22:00")
        ids["sc2"] = shift(ids["cyril"], D3, "06:00", "14:00")
        ids["sc3"] = shift(ids["cyril"], D5, "06:00", "14:00")
        ids["sb1"] = shift(ids["boris"], D1, "06:00", "14:00")

        create_admin("admin", "adminpass1")
        create_manager("mgr_a", "managerpass1", [ids["a"]])
        create_manager("mgr_b", "managerpass1", [ids["b"]])
        create_employee_user(ids["anna"], "emp_anna", "emppass12")
        create_employee_user(ids["cyril"], "emp_cyril", "emppass12")
        create_employee_user(ids["dana"], "emp_dana", "emppass12")
        create_employee_user(ids["boris"], "emp_boris", "emppass12")
        set_employee_active(ids["dana"], 0)

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


def post(client, page, url, data=None, follow=False):
    token = _csrf(client.get(page).get_data(as_text=True))

    return client.post(
        url, data={"csrf_token": token, **(data or {})}, follow_redirects=follow
    )


def propose(client, shift_id, colleague_id, wants_swap=False, note="", follow=False):
    data = {"shift_id": str(shift_id), "colleague_id": str(colleague_id), "note": note}

    if wants_swap:
        data["wants_swap"] = "1"

    return post(client, "/my-swaps", "/my-swaps", data, follow=follow)


def respond(client, swap_id, action, counter=None, follow=False):
    data = {"action": action}

    if counter:
        data["counter_shift_id"] = str(counter)

    return post(client, "/my-swaps", f"/my-swaps/{swap_id}/respond", data, follow=follow)


def decide(client, swap_id, action, note="", follow=False):
    return post(
        client, "/swap-requests", f"/swap-requests/{swap_id}/{action}",
        {"decision_note": note}, follow=follow,
    )


def swaps(app):
    from app.services.shift_swap_service import get_swaps

    with app.app_context():
        return list(reversed(get_swaps()))


def last_swap_id(app):
    rows = swaps(app)
    assert rows
    return rows[-1][0]


def status_of(app, swap_id):
    from app.extensions import db
    from app.orm_models import ShiftSwap

    with app.app_context():
        return db.session.get(ShiftSwap, swap_id).status


def owner_of(app, shift_id):
    from app.extensions import db
    from app.orm_models import Shift

    with app.app_context():
        return db.session.get(Shift, shift_id).employee_id


def to_pending_manager(app, wants_swap=False, counter=None, shift="sa1"):
    """Anna navrhne, Cyril prijme -> čaká na manažéra."""

    ids = app.test_ids
    anna = login(app, "emp_anna", "emppass12")
    cyril = login(app, "emp_cyril", "emppass12")

    propose(anna, ids[shift], ids["cyril"], wants_swap=wants_swap)
    swap_id = last_swap_id(app)
    respond(cyril, swap_id, "accept", counter=counter)

    return swap_id


# ---------------------------------------------------------------------
# Návrh
# ---------------------------------------------------------------------

def test_candidates_and_swappable_shifts(sw):
    from app.services.shift_swap_service import candidate_colleagues, swappable_shifts

    ids = sw.test_ids

    with sw.app_context():
        # Nie sebe, nie neaktívnej (Dana), nie bez účtu (Eva), nie z inej pobočky.
        assert candidate_colleagues(ids["anna"]) == [(ids["cyril"], "Cyril Tretí")]

        offered = [s.id for s in swappable_shifts(ids["anna"])]

    # Bez konceptu a bez minulej smeny.
    assert offered == [ids["sa1"], ids["sa2"], ids["sa_dept"]]


def test_proposal_creates_a_pending_swap_visible_to_both(sw):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")

    response = propose(anna, ids["sa1"], ids["cyril"], note="Mám lekára")
    assert response.status_code == 302

    row = swaps(sw)[0]
    assert row[13] == "pending_colleague"
    assert (row[1], row[4]) == (ids["anna"], ids["cyril"])
    assert D1.isoformat() in row[9] and "06:00–14:00" in row[9]
    assert row[12] == "Mám lekára" and row[11] == 0

    assert owner_of(sw, ids["sa1"]) == ids["anna"]     # zatiaľ sa nič nemení

    cyril_html = login(sw, "emp_cyril", "emppass12").get("/my-swaps").get_data(as_text=True)
    assert "Čaká na tvoju odpoveď" in cyril_html and "Anna" in cyril_html

    assert "Čaká na kolegu" in anna.get("/my-swaps").get_data(as_text=True)


@pytest.mark.parametrize(
    "shift, colleague, message",
    [
        ("sa_draft", "cyril", "zverejnená"),
        ("sa_past", "cyril", "prebehla"),
        ("sc1", "cyril", "nepatrí tomuto zamestnancovi"),   # cudzia smena
        ("sa1", "boris", "z tvojej pobočky"),                # iná pobočka
        ("sa1", "eva", "z tvojej pobočky"),                  # bez účtu
        ("sa1", "dana", "z tvojej pobočky"),                 # neaktívna
        ("sa1", "anna", "z tvojej pobočky"),                 # sám sebe
    ],
)
def test_invalid_proposals_are_rejected(sw, shift, colleague, message):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")

    response = propose(anna, ids[shift], ids[colleague], follow=True)

    assert message in response.get_data(as_text=True)
    assert swaps(sw) == []


def test_unknown_ids_do_not_break(sw):
    anna = login(sw, "emp_anna", "emppass12")
    token = _csrf(anna.get("/my-swaps").get_data(as_text=True))

    for data in ({}, {"shift_id": "999999", "colleague_id": "999999"}, {"shift_id": "x"}):
        response = anna.post("/my-swaps", data={"csrf_token": token, **data})
        assert response.status_code == 200

    assert swaps(sw) == []


def test_a_shift_can_be_in_only_one_open_swap(sw):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")

    propose(anna, ids["sa1"], ids["cyril"])
    response = propose(anna, ids["sa1"], ids["cyril"], follow=True)
    assert "už v inej otvorenej žiadosti" in response.get_data(as_text=True)
    assert len(swaps(sw)) == 1

    # Po odmietnutí kolegom sa dá ponúknuť znova.
    respond(login(sw, "emp_cyril", "emppass12"), last_swap_id(sw), "decline")
    assert propose(anna, ids["sa1"], ids["cyril"]).status_code == 302
    assert len(swaps(sw)) == 2


# ---------------------------------------------------------------------
# Odpoveď kolegu
# ---------------------------------------------------------------------

def test_colleague_declines(sw):
    ids = sw.test_ids
    propose(login(sw, "emp_anna", "emppass12"), ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)

    respond(login(sw, "emp_cyril", "emppass12"), swap_id, "decline")

    assert status_of(sw, swap_id) == "declined"
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


def test_only_the_named_colleague_can_respond(sw):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")
    propose(anna, ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)

    for client in (anna, login(sw, "emp_boris", "emppass12")):
        response = respond(client, swap_id, "accept", follow=True)
        assert "Žiadosť neexistuje" in response.get_data(as_text=True)

    assert status_of(sw, swap_id) == "pending_colleague"


def test_invalid_action_and_double_response(sw):
    ids = sw.test_ids
    propose(login(sw, "emp_anna", "emppass12"), ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)
    cyril = login(sw, "emp_cyril", "emppass12")

    assert "prijímaš alebo odmietaš" in respond(cyril, swap_id, "nieco", follow=True).get_data(as_text=True)

    respond(cyril, swap_id, "accept")
    response = respond(cyril, swap_id, "decline", follow=True)
    assert "už odpovedalo" in response.get_data(as_text=True)
    assert status_of(sw, swap_id) == "pending_manager"


def test_exchange_requires_a_valid_counter_shift(sw):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")
    cyril = login(sw, "emp_cyril", "emppass12")

    propose(anna, ids["sa1"], ids["cyril"], wants_swap=True)
    swap_id = last_swap_id(sw)

    assert "Vyber svoju smenu" in respond(cyril, swap_id, "accept", follow=True).get_data(as_text=True)

    # Cudzia smena (Annina, Borisova) nesmie byť protihodnotou.
    for wrong in (ids["sa2"], ids["sb1"]):
        response = respond(cyril, swap_id, "accept", counter=wrong, follow=True)
        assert "Smena už neexistuje alebo nepatrí" in response.get_data(as_text=True)

    # Neexistujúca smena sa berie ako nevybratá.
    response = respond(cyril, swap_id, "accept", counter=999999, follow=True)
    assert "Vyber svoju smenu" in response.get_data(as_text=True)

    assert status_of(sw, swap_id) == "pending_colleague"

    respond(cyril, swap_id, "accept", counter=ids["sc2"])
    assert status_of(sw, swap_id) == "pending_manager"
    assert swaps(sw)[0][10] and D3.isoformat() in swaps(sw)[0][10]


def test_counter_shift_cannot_be_in_another_open_swap(sw):
    ids = sw.test_ids
    cyril = login(sw, "emp_cyril", "emppass12")
    anna = login(sw, "emp_anna", "emppass12")

    # Cyril už ponúka sc2 Anne.
    propose(cyril, ids["sc2"], ids["anna"])

    propose(anna, ids["sa1"], ids["cyril"], wants_swap=True)
    swap_id = [r for r in swaps(sw) if r[1] == ids["anna"]][0][0]

    response = respond(cyril, swap_id, "accept", counter=ids["sc2"], follow=True)
    assert "už v inej otvorenej žiadosti" in response.get_data(as_text=True)


def test_response_cancels_the_swap_if_the_offered_shift_changed(sw):
    from app.services.shift_service import delete_shift

    ids = sw.test_ids
    propose(login(sw, "emp_anna", "emppass12"), ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)

    with sw.app_context():
        delete_shift(ids["sa1"])

    response = respond(login(sw, "emp_cyril", "emppass12"), swap_id, "accept", follow=True)

    assert "Žiadosť sa zrušila" in response.get_data(as_text=True)
    assert status_of(sw, swap_id) == "cancelled"


# ---------------------------------------------------------------------
# Zrušenie
# ---------------------------------------------------------------------

def test_cancel_by_either_party_and_not_by_others(sw):
    ids = sw.test_ids
    anna = login(sw, "emp_anna", "emppass12")
    cyril = login(sw, "emp_cyril", "emppass12")
    boris = login(sw, "emp_boris", "emppass12")

    propose(anna, ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)

    response = post(boris, "/my-swaps", f"/my-swaps/{swap_id}/cancel", follow=True)
    assert "Žiadosť neexistuje" in response.get_data(as_text=True)
    assert status_of(sw, swap_id) == "pending_colleague"

    post(anna, "/my-swaps", f"/my-swaps/{swap_id}/cancel")
    assert status_of(sw, swap_id) == "cancelled"

    # Kolega môže zrušiť aj žiadosť, ktorú už prijal (čaká na manažéra).
    swap_two = to_pending_manager(sw, shift="sa2")
    post(cyril, "/my-swaps", f"/my-swaps/{swap_two}/cancel")
    assert status_of(sw, swap_two) == "cancelled"


def test_approved_swap_cannot_be_cancelled(sw):
    swap_id = to_pending_manager(sw)
    decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve")

    response = post(
        login(sw, "emp_anna", "emppass12"), "/my-swaps",
        f"/my-swaps/{swap_id}/cancel", follow=True,
    )

    assert "už nemožno zrušiť" in response.get_data(as_text=True)
    assert status_of(sw, swap_id) == "approved"


# ---------------------------------------------------------------------
# Schválenie manažérom
# ---------------------------------------------------------------------

def test_handover_is_applied_only_after_manager_approval(sw):
    ids = sw.test_ids
    swap_id = to_pending_manager(sw)

    assert owner_of(sw, ids["sa1"]) == ids["anna"]       # kolega súhlasil, ale ešte nič

    manager = login(sw, "mgr_a", "managerpass1")
    html = manager.get("/swap-requests").get_data(as_text=True)
    assert "Anna" in html and "Cyril" in html and "bez protihodnoty" in html

    response = decide(manager, swap_id, "approve", note="OK", follow=True)
    assert "schválená" in response.get_data(as_text=True)

    assert status_of(sw, swap_id) == "approved"
    assert owner_of(sw, ids["sa1"]) == ids["cyril"]
    assert swaps(sw)[0][17] == "mgr_a" and swaps(sw)[0][18] == "OK"

    # Smena ostáva zverejnená a kolega ju vidí vo svojom rozpise.
    assert D1.isoformat() in login(sw, "emp_cyril", "emppass12").get("/my-schedule").get_data(as_text=True)
    assert D1.isoformat() not in login(sw, "emp_anna", "emppass12").get("/my-schedule").get_data(as_text=True)


def test_exchange_swaps_both_shifts(sw):
    ids = sw.test_ids
    swap_id = to_pending_manager(sw, wants_swap=True, counter=ids["sc3"])

    decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve")

    assert status_of(sw, swap_id) == "approved"
    assert owner_of(sw, ids["sa1"]) == ids["cyril"]
    assert owner_of(sw, ids["sc3"]) == ids["anna"]


def test_manager_can_reject_only_with_a_reason(sw):
    ids = sw.test_ids
    swap_id = to_pending_manager(sw)
    manager = login(sw, "mgr_a", "managerpass1")

    response = decide(manager, swap_id, "reject", note=" ", follow=True)
    assert "uveď dôvod" in response.get_data(as_text=True)
    assert status_of(sw, swap_id) == "pending_manager"

    decide(manager, swap_id, "reject", note="Treba obsadiť linku")

    assert status_of(sw, swap_id) == "rejected"
    assert owner_of(sw, ids["sa1"]) == ids["anna"]

    html = login(sw, "emp_anna", "emppass12").get("/my-swaps").get_data(as_text=True)
    assert "Zamietnutá manažérom" in html and "Treba obsadiť linku" in html


def test_manager_cannot_decide_a_swap_that_is_not_waiting_for_them(sw):
    ids = sw.test_ids
    propose(login(sw, "emp_anna", "emppass12"), ids["sa1"], ids["cyril"])
    swap_id = last_swap_id(sw)

    manager = login(sw, "mgr_a", "managerpass1")

    assert "nečaká na manažéra" in decide(manager, swap_id, "approve", follow=True).get_data(as_text=True)
    assert "nečaká na manažéra" in decide(manager, swap_id, "reject", note="x", follow=True).get_data(as_text=True)
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


def _only_pending(app, swap_id):
    assert status_of(app, swap_id) == "pending_manager"


def test_approval_blocked_by_overlapping_shift(sw):
    from app.services.shift_service import add_shift

    ids = sw.test_ids

    with sw.app_context():
        add_shift(ids["cyril"], D1.isoformat(), "10:00", "18:00", "Poobedná", published=True)

    swap_id = to_pending_manager(sw)
    response = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True)
    text = response.get_data(as_text=True)

    assert "Výmenu nemožno schváliť" in text
    assert "Cyril Tretí" in text and "už má v tomto čase inú smenu" in text

    _only_pending(sw, swap_id)
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


def test_approval_blocked_by_absence(sw):
    from app.services.absence_service import add_absence

    ids = sw.test_ids

    with sw.app_context():
        add_absence(ids["cyril"], "Dovolenka", D1.isoformat(), D1.isoformat())

    swap_id = to_pending_manager(sw)
    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)

    assert "neprítomnosť" in text
    _only_pending(sw, swap_id)
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


def test_approval_blocked_by_weekly_hours_limit(sw):
    from app.extensions import db
    from app.orm_models import Employee

    ids = sw.test_ids

    with sw.app_context():
        db.session.get(Employee, ids["cyril"]).weekly_hours = 16    # už má 2 x 8 h
        db.session.commit()

    swap_id = to_pending_manager(sw)
    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)

    assert "týždenný pracovný fond" in text
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


def test_approval_blocked_when_the_new_owner_is_not_assigned_to_the_department(sw):
    ids = sw.test_ids
    swap_id = to_pending_manager(sw, shift="sa_dept")

    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)

    assert "nie je priradený k vybranému oddeleniu" in text
    assert owner_of(sw, ids["sa_dept"]) == ids["anna"]


def test_exchange_is_validated_in_both_directions_and_rolled_back(sw):
    """Anna by dostala sc2 (D3 06-14), ale tam už má koncept sa_draft."""

    ids = sw.test_ids
    swap_id = to_pending_manager(sw, wants_swap=True, counter=ids["sc2"])

    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)

    assert "Anna Prvá" in text and "už má v tomto čase inú smenu" in text

    # Nič sa nezmenilo - ani prvá (inak platná) polovica výmeny.
    assert owner_of(sw, ids["sa1"]) == ids["anna"]
    assert owner_of(sw, ids["sc2"]) == ids["cyril"]
    _only_pending(sw, swap_id)


def test_approval_blocked_when_a_shift_changed_after_acceptance(sw):
    from app.extensions import db
    from app.orm_models import Shift
    from app.services.shift_service import delete_shift

    ids = sw.test_ids

    # a) ponúkaná smena sa medzitým stiahla do konceptu
    swap_id = to_pending_manager(sw)

    with sw.app_context():
        db.session.get(Shift, ids["sa1"]).published = 0
        db.session.commit()

    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)
    assert "Ponúkaná smena" in text and "zverejnená" in text
    assert "Žiadosť zamietni alebo požiadaj o novú" in text
    _only_pending(sw, swap_id)

    # b) smena sa zmazala (odkaz sa vynuluje, žiadosť ostane v histórii)
    swap_two = to_pending_manager(sw, shift="sa2")

    with sw.app_context():
        delete_shift(ids["sa2"])

    text = decide(login(sw, "mgr_a", "managerpass1"), swap_two, "approve", follow=True).get_data(as_text=True)
    assert "Smena už neexistuje" in text
    assert "sa2" not in text
    assert [r[9] for r in swaps(sw) if r[0] == swap_two][0].startswith(D2.isoformat())


def test_approval_blocked_when_an_employee_was_deactivated(sw):
    from app.services.employee_service import set_employee_active

    ids = sw.test_ids
    swap_id = to_pending_manager(sw)

    with sw.app_context():
        set_employee_active(ids["cyril"], 0)

    text = decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", follow=True).get_data(as_text=True)

    assert "už nie je aktívny" in text
    assert owner_of(sw, ids["sa1"]) == ids["anna"]


# ---------------------------------------------------------------------
# Práva a viditeľnosť
# ---------------------------------------------------------------------

def test_managers_are_limited_to_their_branches(sw):
    ids = sw.test_ids
    swap_id = to_pending_manager(sw)

    mgr_b = login(sw, "mgr_b", "managerpass1")
    html = mgr_b.get("/swap-requests").get_data(as_text=True)
    assert "Anna" not in html

    token = _csrf(html)
    for action in ("approve", "reject"):
        response = mgr_b.post(
            f"/swap-requests/{swap_id}/{action}",
            data={"csrf_token": token, "decision_note": "x"},
        )
        assert response.status_code == 404

    assert owner_of(sw, ids["sa1"]) == ids["anna"]

    assert "Anna" in login(sw, "admin", "adminpass1").get("/swap-requests").get_data(as_text=True)
    assert "Anna" in login(sw, "mgr_a", "managerpass1").get("/swap-requests").get_data(as_text=True)


def test_employees_cannot_open_manager_pages_and_pure_managers_cannot_swap(sw):
    swap_id = to_pending_manager(sw)

    anna = login(sw, "emp_anna", "emppass12")
    assert anna.get("/swap-requests").status_code == 403

    token = _csrf(anna.get("/my-swaps").get_data(as_text=True))
    for action in ("approve", "reject"):
        response = anna.post(
            f"/swap-requests/{swap_id}/{action}",
            data={"csrf_token": token, "decision_note": "x"},
        )
        assert response.status_code == 403

    assert login(sw, "mgr_a", "managerpass1").get("/my-swaps").status_code == 403

    admin = login(sw, "admin", "adminpass1").get("/my-swaps")
    assert admin.status_code == 302


def test_involved_manager_cannot_approve_own_swap_but_admin_can(sw):
    from app.services.auth_service import set_manager_function

    ids = sw.test_ids

    with sw.app_context():
        set_manager_function(ids["anna"], True, [ids["a"]])

    swap_id = to_pending_manager(sw)

    anna_manager = login(sw, "emp_anna", "emppass12")
    html = anna_manager.get("/swap-requests").get_data(as_text=True)
    assert "vybaví iný manažér alebo admin" in html

    response = decide(anna_manager, swap_id, "approve", follow=True)
    assert "nemôžeš vybaviť sám" in response.get_data(as_text=True)
    assert owner_of(sw, ids["sa1"]) == ids["anna"]

    decide(login(sw, "admin", "adminpass1"), swap_id, "approve")
    assert owner_of(sw, ids["sa1"]) == ids["cyril"]


def test_employees_see_only_their_own_swaps(sw):
    to_pending_manager(sw)

    boris_html = login(sw, "emp_boris", "emppass12").get("/my-swaps").get_data(as_text=True)

    assert "Anna" not in boris_html and "Cyril" not in boris_html


def test_navigation_badges(sw):
    ids = sw.test_ids
    cyril = login(sw, "emp_cyril", "emppass12")
    manager = login(sw, "mgr_a", "managerpass1")

    assert "Výmeny smien (" not in cyril.get("/my-schedule").get_data(as_text=True)

    propose(login(sw, "emp_anna", "emppass12"), ids["sa1"], ids["cyril"])
    assert "Výmeny smien (1)" in cyril.get("/my-schedule").get_data(as_text=True)
    assert "Výmeny smien (" not in manager.get("/").get_data(as_text=True)

    respond(cyril, last_swap_id(sw), "accept")
    assert "Výmeny smien (1)" in manager.get("/").get_data(as_text=True)
    assert "Výmeny smien (" not in cyril.get("/my-schedule").get_data(as_text=True)

    # Manažér z inej pobočky počet nevidí.
    assert "Výmeny smien (" not in login(sw, "mgr_b", "managerpass1").get("/").get_data(as_text=True)


# ---------------------------------------------------------------------
# Záznamy zmien, stránky, migrácia
# ---------------------------------------------------------------------

def test_swap_is_audited(sw):
    from app.extensions import db
    from app.orm_models import AuditLog

    ids = sw.test_ids
    swap_id = to_pending_manager(sw)
    decide(login(sw, "mgr_a", "managerpass1"), swap_id, "approve", note="v poriadku")

    with sw.app_context():
        rows = db.session.query(AuditLog).order_by(AuditLog.id).all()
        entries = [(r.action, r.username, r.summary, r.branch_id) for r in rows]

    swap_entries = [e for e in entries if e[0].startswith("swap.")]
    assert swap_entries[0][:2] == ("swap.create", "emp_anna")
    assert "Anna Prvá → Cyril Tretí" in swap_entries[0][2]
    assert swap_entries[0][3] == ids["a"]
    assert any("prijatá" in e[2].lower() or "čaká na schválenie" in e[2].lower() for e in swap_entries)
    assert swap_entries[-1][:2] == ("swap.update", "mgr_a")
    assert "schválená" in swap_entries[-1][2] and "v poriadku" in swap_entries[-1][2]

    shift_changes = [e for e in entries if e[0] == "shift.update" and e[1] == "mgr_a"]
    assert any("zamestnanec: Anna Prvá → Cyril Tretí" in e[2] for e in shift_changes)


def test_pages_render(sw):
    to_pending_manager(sw)

    for user, password, urls in (
        ("emp_anna", "emppass12", ["/my-swaps", "/my-schedule"]),
        ("emp_cyril", "emppass12", ["/my-swaps"]),
        ("mgr_a", "managerpass1", ["/swap-requests", "/swap-requests?status=all",
                                   "/swap-requests?status=nezmysel", "/audit"]),
        ("admin", "adminpass1", ["/swap-requests", "/swap-requests?status=approved"]),
    ):
        client = login(sw, user, password)

        for url in urls:
            assert client.get(url).status_code == 200, (user, url)


def test_swap_table_migration_roundtrip(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "swapmig.db")

    with app.app_context():
        upgrade()
        assert "shift_swaps" in sa.inspect(db.engine).get_table_names()

        downgrade(revision="f7a8b9c0d132")
        assert "shift_swaps" not in sa.inspect(db.engine).get_table_names()
        assert "audit_log" in sa.inspect(db.engine).get_table_names()

        upgrade()
        assert "shift_swaps" in sa.inspect(db.engine).get_table_names()
