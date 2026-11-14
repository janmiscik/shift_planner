"""Testy ochrany pred súbežnými zápismi smien (dvaja manažéri naraz).

Scenár: kontrola pravidiel (prekrytie, fond...) a zápis sú dva kroky.
Bez zámku môžu dve súbežné požiadavky OBE prejsť kontrolou (vidia ešte
starý stav) a obe zapísať - vzniknú kolidujúce smeny. Testy to overujú
na skutočných vláknach proti rovnakej (SQLite) databáze:

  1. najprv sa dokáže, že problém reálne existuje (so zámkom vypnutým),
  2. potom sa overí, že so zámkom (predvolené správanie appky) sa to
     nestane - jedna požiadavka uspeje, druhá uvidí už zmenený stav a
     kontrola ju správne odmietne.
"""

import re
import threading
import time
from contextlib import nullcontext
from datetime import date, timedelta

import pytest

TODAY = date.today()
MON = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
SHIFT_DAY = MON.isoformat()


def csrf_from(html):
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa nenašiel")


def login(app, username, password):
    client = app.test_client()
    token = csrf_from(client.get("/login").get_data(as_text=True))
    response = client.post(
        "/login", data={"csrf_token": token, "username": username, "password": password}
    )
    assert response.status_code == 302

    return client


def build_world(tmp_path, db_name):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / db_name)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    ids = {}

    with app.app_context():
        db.create_all()

        from app.services.auth_service import create_manager
        from app.services.branch_service import add_branch
        from app.services.employee_service import add_employee

        ids["branch_id"] = add_branch("Košice")
        ids["employee_id"] = add_employee("Anna", "Prvá", "Op", 40, ids["branch_id"])
        ids["other_employee_id"] = add_employee("Boris", "Druhý", "Op", 40, ids["branch_id"])

        create_manager("mgr1", "managerpass1", [ids["branch_id"]])
        create_manager("mgr2", "managerpass1", [ids["branch_id"]])

    app.test_ids = ids

    return app


def shifts_in_db(app):
    from app.orm_models import Shift
    from app.extensions import db

    with app.app_context():
        rows = db.session.query(Shift).order_by(Shift.id).all()
        return [(r.employee_id, r.shift_date, r.start_time, r.end_time) for r in rows]


def overlapping(rows):
    """Overí, či medzi zadanými smenami (toho istého zamestnanca, toho
    istého dňa) je prekrytie časov."""

    by_day = {}

    for employee_id, shift_date, start, end in rows:
        by_day.setdefault((employee_id, shift_date), []).append((start, end))

    for (_employee, _day), windows in by_day.items():
        if len(windows) < 2:
            continue

        for i, (start_a, end_a) in enumerate(windows):
            for start_b, end_b in windows[i + 1:]:
                if max(start_a, start_b) < min(end_a, end_b):
                    return True

    return False


def add_shift_payload(employee_id, start="06:00", end="14:00", shift_date=SHIFT_DAY):
    return {
        "employee_id": str(employee_id), "department_id": "", "shift_date": shift_date,
        "start_time": start, "end_time": end, "shift_type": "Ranná",
    }


def run_concurrent_add_shift(app, slow_seconds, lock_disabled):
    """Dvaja manažéri (dve vlákna) sa súčasne pokúšajú pridať
    PREKRÝVAJÚCU sa smenu tomu istému zamestnancovi. Kontrola pravidiel
    sa umelo spomalí, aby sa časové okno medzi "over" a "zapíš" zväčšilo.
    Vráti zoznam stavových kódov odpovedí (v poradí dokončenia)."""

    import app.web.routes.shifts as shifts_module

    real_validate = shifts_module.validate_shift

    def slow_validate(*args, **kwargs):
        result = real_validate(*args, **kwargs)
        time.sleep(slow_seconds)

        return result

    results = {}
    barrier = threading.Barrier(2)
    employee_id = app.test_ids["employee_id"]

    def worker(username, offset_minutes):
        client = login(app, username, "managerpass1")
        start = f"{6 + offset_minutes // 60:02d}:{offset_minutes % 60:02d}"
        payload = add_shift_payload(employee_id, start=start, end="15:00")

        token = csrf_from(client.get("/shifts/add").get_data(as_text=True))
        payload["csrf_token"] = token

        barrier.wait(timeout=5)                 # obe vlákna odštartujú naraz
        response = client.post("/shifts/add", data=payload)
        results[username] = response.status_code

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(shifts_module, "validate_shift", slow_validate)

        if lock_disabled:
            monkeypatch.setattr(
                shifts_module, "lock_employee_for_write", lambda employee_id: nullcontext()
            )

        threads = [
            threading.Thread(target=worker, args=("mgr1", 0)),
            threading.Thread(target=worker, args=("mgr2", 1)),   # prekrývajúci sa čas (06:01-15:00)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive(), "vlákno sa zaseklo (možný deadlock zámku)"

    return results


# ---------------------------------------------------------------------
# Dôkaz problému (zámok zámerne vypnutý)
# ---------------------------------------------------------------------

def test_without_the_lock_two_concurrent_requests_can_create_overlapping_shifts(tmp_path):
    app = build_world(tmp_path, "race_unsafe.db")

    results = run_concurrent_add_shift(app, slow_seconds=0.3, lock_disabled=True)

    rows = shifts_in_db(app)
    assert len(rows) == 2, "test by mal vytvoriť presne 2 (kolidujúce) smeny bez zámku"
    assert list(results.values()) == [302, 302], "bez zámku obe požiadavky prešli kontrolou"
    assert overlapping(rows), (
        "očakávaná kolízia sa nevytvorila - skús zväčšiť slow_seconds "
        "(timing-dependent dôkaz problému)"
    )


# ---------------------------------------------------------------------
# So zámkom (predvolené správanie appky) sa to nestane
# ---------------------------------------------------------------------

def test_the_lock_serializes_concurrent_requests_for_the_same_employee(tmp_path):
    app = build_world(tmp_path, "race_safe.db")

    results = run_concurrent_add_shift(app, slow_seconds=0.3, lock_disabled=False)

    rows = shifts_in_db(app)

    assert not overlapping(rows), "so zámkom nesmú vzniknúť kolidujúce smeny"
    assert len(rows) == 1, "presne jedna z dvoch kolidujúcich požiadaviek smie uspieť"
    assert sorted(results.values()) == [200, 302], (
        "jedna požiadavka uspeje (302), druhá uvidí už zapísanú smenu a "
        "kontrola ju odmietne (200 - formulár s chybou)"
    )


def test_the_lock_does_not_block_requests_for_different_employees(tmp_path):
    """Zámok je na zamestnanca, nie globálny - súbežné zmeny DVOCH
    rôznych zamestnancov sa navzájom nečakajú a obe uspejú."""

    app = build_world(tmp_path, "race_other.db")

    import app.web.routes.shifts as shifts_module

    real_validate = shifts_module.validate_shift

    def slow_validate(*args, **kwargs):
        time.sleep(0.2)
        return real_validate(*args, **kwargs)

    results = {}
    barrier = threading.Barrier(2)

    def worker(username, employee_key):
        client = login(app, username, "managerpass1")
        payload = add_shift_payload(app.test_ids[employee_key])
        token = csrf_from(client.get("/shifts/add").get_data(as_text=True))
        payload["csrf_token"] = token

        barrier.wait(timeout=5)
        results[username] = client.post("/shifts/add", data=payload).status_code

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(shifts_module, "validate_shift", slow_validate)

        threads = [
            threading.Thread(target=worker, args=("mgr1", "employee_id")),
            threading.Thread(target=worker, args=("mgr2", "other_employee_id")),
        ]
        [t.start() for t in threads]
        [t.join(timeout=10) for t in threads]

    assert results == {"mgr1": 302, "mgr2": 302}
    assert len(shifts_in_db(app)) == 2


# ---------------------------------------------------------------------
# Presun smeny (drag-and-drop) chránený rovnakým zámkom
# ---------------------------------------------------------------------

def test_move_and_add_for_the_same_employee_are_serialized(tmp_path):
    """Jeden manažér presúva existujúcu smenu, druhý súčasne pridáva
    novú, prekrývajúcu sa s CIEĽOVÝM časom presunu - obe cesty
    (``/shifts/add`` aj presun v kalendári) musia zdieľať ten istý
    zámok na zamestnanca."""

    app = build_world(tmp_path, "race_move.db")
    employee_id = app.test_ids["employee_id"]

    from app.services.shift_service import add_shift

    with app.app_context():
        existing_id = add_shift(employee_id, SHIFT_DAY, "10:00", "18:00", "Ranná", published=True)

    import app.web.routes.calendar_views as calendar_module
    import app.web.routes.shifts as shifts_module

    real_move_validate = calendar_module.validate_shift
    real_add_validate = shifts_module.validate_shift

    def slow(fn):
        def wrapped(*args, **kwargs):
            result = fn(*args, **kwargs)
            time.sleep(0.3)
            return result

        return wrapped

    results = {}
    barrier = threading.Barrier(2)

    def move_worker():
        client = login(app, "mgr1", "managerpass1")
        token = csrf_from(client.get("/calendar").get_data(as_text=True))

        barrier.wait(timeout=5)
        response = client.post(
            f"/api/shifts/{existing_id}/move",
            json={"shift_date": SHIFT_DAY, "start_time": "06:00", "end_time": "14:00"},
            headers={"X-CSRFToken": token},
        )
        results["move"] = response.status_code

    def add_worker():
        client = login(app, "mgr2", "managerpass1")
        payload = add_shift_payload(employee_id, start="06:00", end="14:00")
        token = csrf_from(client.get("/shifts/add").get_data(as_text=True))
        payload["csrf_token"] = token

        barrier.wait(timeout=5)
        results["add"] = client.post("/shifts/add", data=payload).status_code

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(calendar_module, "validate_shift", slow(real_move_validate))
        monkeypatch.setattr(shifts_module, "validate_shift", slow(real_add_validate))

        threads = [threading.Thread(target=move_worker), threading.Thread(target=add_worker)]
        [t.start() for t in threads]
        [t.join(timeout=10) for t in threads]

    rows = shifts_in_db(app)
    assert not overlapping(rows)
    # Presne jedna z dvoch akcií (presun na 06-14, alebo nová smena 06-14) uspela.
    assert sorted(results.values()) in ([200, 200], [200, 400])


# ---------------------------------------------------------------------
# Výmena smien: obaja zúčastnení zamestnanci sú chránení
# ---------------------------------------------------------------------

def test_two_approvals_sharing_an_employee_do_not_create_overlapping_shifts(tmp_path):
    """Anna je proposer vo výmene 1 aj kolega vo výmene 2; obe čakajú na
    schválenie a manažér ich schvaľuje takmer súčasne. Výsledný rozpis
    Anny nesmie obsahovať kolidujúce smeny."""

    from app.extensions import db
    from app.services.auth_service import create_employee_user, create_manager
    from app.services.employee_service import add_employee
    from app.services.shift_service import add_shift
    from app.services.shift_swap_service import approve_swap, colleague_respond, create_swap

    app = build_world(tmp_path, "race_swap.db")

    with app.app_context():
        branch_id = app.test_ids["branch_id"]
        anna = app.test_ids["employee_id"]
        cyril = add_employee("Cyril", "Tretí", "Op", 40, branch_id)
        dana = add_employee("Dana", "Štvrtá", "Op", 40, branch_id)
        # create_swap vyžaduje, aby kolega mal prihlasovací účet.
        create_employee_user(anna, "emp_anna", "emppass12")
        create_employee_user(cyril, "emp_cyril", "emppass12")
        create_employee_user(dana, "emp_dana", "emppass12")
        create_manager("mgr3", "managerpass1", [branch_id])

        # Výmena 1: Cyril ponúka Anne svoju smenu 06-14 (dnes+0).
        cyril_shift = add_shift(cyril, SHIFT_DAY, "06:00", "14:00", "Ranná", published=True)
        swap1, error1 = create_swap(cyril, anna, cyril_shift)
        assert error1 is None, error1
        ok1, error1b = colleague_respond(swap1, anna, True)
        assert ok1, error1b

        # Výmena 2: Dana ponúka Anne inú smenu, TIEŽ 06-14 v ten istý deň.
        dana_shift = add_shift(dana, SHIFT_DAY, "06:00", "14:00", "Poobedná", published=True)
        swap2, error2 = create_swap(dana, anna, dana_shift)
        assert error2 is None, error2
        ok2, error2b = colleague_respond(swap2, anna, True)
        assert ok2, error2b

        db.session.remove()

    import app.services.shift_swap_service as swap_module

    real_validate = swap_module.validate_shift

    def slow_validate(*args, **kwargs):
        result = real_validate(*args, **kwargs)
        time.sleep(0.3)
        return result

    outcomes = {}
    barrier = threading.Barrier(2)

    def approve_worker(key, swap_id):
        with app.app_context():
            barrier.wait(timeout=5)
            ok, messages = approve_swap(swap_id, None)
            outcomes[key] = (ok, messages)
            db.session.remove()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(swap_module, "validate_shift", slow_validate)

        threads = [
            threading.Thread(target=approve_worker, args=("swap1", swap1)),
            threading.Thread(target=approve_worker, args=("swap2", swap2)),
        ]
        [t.start() for t in threads]
        [t.join(timeout=10) for t in threads]

    with app.app_context():
        from app.services.shift_service import get_shifts_by_employee

        anna_rows = [(anna, s[3], s[4], s[5]) for s in get_shifts_by_employee(anna)]

    assert not overlapping(anna_rows)
    # Presne jedna z dvoch výmen mohla uspieť (obe dávajú Anne 06-14 v ten istý deň).
    assert sorted(ok for ok, _ in outcomes.values()) == [False, True]


# ---------------------------------------------------------------------
# Samotný zamykací mechanizmus (bez HTTP vrstvy)
# ---------------------------------------------------------------------

def test_lock_employees_for_write_locks_in_ascending_order_to_avoid_deadlock(tmp_path):
    from app.services.concurrency import lock_employees_for_write

    app = build_world(tmp_path, "lock_order.db")

    with app.app_context():
        order = []

        import app.services.concurrency as concurrency_module
        import contextlib

        original = concurrency_module.lock_employee_for_write

        @contextlib.contextmanager
        def recording_lock(employee_id):
            order.append(employee_id)

            with original(employee_id):
                yield

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(concurrency_module, "lock_employee_for_write", recording_lock)

            with lock_employees_for_write([5, 2, 8, 2]):
                pass

        assert order == [2, 5, 8]


def test_two_threads_locking_the_same_pair_in_opposite_order_do_not_deadlock(tmp_path):
    """Klasický scenár krížového zámku: vlákno A chce zamknúť (1, 2),
    vlákno B (2, 1). Vďaka triedeniu podľa ID obe v skutočnosti zamykajú
    v rovnakom poradí, takže sa nezaseknú."""

    from app.services.concurrency import lock_employees_for_write

    app = build_world(tmp_path, "lock_deadlock.db")
    finished = []

    def worker(order):
        with app.app_context():
            with lock_employees_for_write(order):
                time.sleep(0.2)

            finished.append(order)

    with app.app_context():
        from app.services.employee_service import add_employee

        e1 = add_employee("E1", "X", "Op", 40, app.test_ids["branch_id"])
        e2 = add_employee("E2", "X", "Op", 40, app.test_ids["branch_id"])

    threads = [
        threading.Thread(target=worker, args=([e1, e2],)),
        threading.Thread(target=worker, args=([e2, e1],)),
    ]
    [t.start() for t in threads]
    [t.join(timeout=5) for t in threads]

    assert len(finished) == 2, "zámok sa zasekol (deadlock) namiesto serializácie"


def test_lock_times_out_instead_of_hanging_forever(tmp_path, monkeypatch):
    """Ak zámok z nejakého dôvodu nikdy nepríde, appka po rozumnom čase
    vyhodí zrozumiteľnú chybu namiesto toho, aby visela donekonečna."""

    import app.services.concurrency as concurrency_module

    # SQLite "čaká, kým druhá transakcia skončí" (PRAGMA busy_timeout) aj
    # na úrovni samotného DBAPI pripojenia - musí byť kratšia než náš
    # vonkajší LOCK_TIMEOUT_SECONDS, inak by prvý pokus čakal dlhšie, než
    # náš kód vôbec stihne vyhodnotiť vlastný deadline.
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT", "0.2")
    app = build_world(tmp_path, "lock_timeout.db")
    monkeypatch.setattr(concurrency_module, "LOCK_TIMEOUT_SECONDS", 1)

    with app.app_context():
        holder_ready = threading.Event()
        release = threading.Event()

        def holder():
            with app.app_context():
                with concurrency_module.lock_employee_for_write(app.test_ids["employee_id"]):
                    holder_ready.set()
                    release.wait(timeout=5)

        thread = threading.Thread(target=holder)
        thread.start()
        holder_ready.wait(timeout=5)

        try:
            with pytest.raises(concurrency_module.ShiftLockTimeout):
                with concurrency_module.lock_employee_for_write(app.test_ids["employee_id"]):
                    pass
        finally:
            release.set()
            thread.join(timeout=5)


def test_lock_is_released_after_the_block_so_a_later_request_can_proceed(tmp_path):
    from app.services.concurrency import lock_employee_for_write

    app = build_world(tmp_path, "lock_release.db")
    employee_id = app.test_ids["employee_id"]

    with app.app_context():
        with lock_employee_for_write(employee_id):
            pass

        # Druhé, neskoršie (nie súbežné) použitie prebehne bez čakania.
        started = time.monotonic()

        with lock_employee_for_write(employee_id):
            pass

        assert time.monotonic() - started < 1
