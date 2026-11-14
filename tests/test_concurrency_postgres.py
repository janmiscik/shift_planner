"""Testy ochrany pred súbežnými zápismi smien - PostgreSQL vetva
(``pg_advisory_xact_lock``).

Spúšťajú sa len ak je nastavené ``TEST_DATABASE_URL`` (pozri
``tests/conftest.py``). Rovnaký scenár ako ``tests/test_concurrency.py``
(SQLite), tu overený proti skutočnému PostgreSQL serveru.
"""

import os
import re
import threading
import time
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL nie je nastavené",
)

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


@pytest.fixture
def pg_app():
    from app.extensions import db
    from app.web.app import create_app

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    with app.app_context():
        db.create_all()

        from app.services.auth_service import create_manager
        from app.services.branch_service import add_branch
        from app.services.employee_service import add_employee

        branch_id = add_branch("Košice")
        employee_id = add_employee("Anna", "Prvá", "Op", 40, branch_id)
        create_manager("mgr1", "managerpass1", [branch_id])
        create_manager("mgr2", "managerpass1", [branch_id])

    app.test_ids = {"branch_id": branch_id, "employee_id": employee_id}

    return app


def shifts_in_db(app):
    from app.extensions import db
    from app.orm_models import Shift

    with app.app_context():
        rows = db.session.query(Shift).order_by(Shift.id).all()
        return [(r.employee_id, r.shift_date, r.start_time, r.end_time) for r in rows]


def test_the_lock_uses_a_real_postgres_advisory_lock_and_serializes_requests(pg_app):
    """Rovnaký "dvaja manažéri naraz" scenár ako pri SQLite, tentoraz
    proti skutočnému PostgreSQL. Kontrola pravidiel sa umelo spomalí,
    aby sa časové okno medzi "over" a "zapíš" zväčšilo."""

    import app.web.routes.shifts as shifts_module

    real_validate = shifts_module.validate_shift

    def slow_validate(*args, **kwargs):
        result = real_validate(*args, **kwargs)
        time.sleep(0.3)
        return result

    employee_id = pg_app.test_ids["employee_id"]
    results = {}
    barrier = threading.Barrier(2)

    def worker(username, offset_minutes):
        client = login(pg_app, username, "managerpass1")
        start = f"{6 + offset_minutes // 60:02d}:{offset_minutes % 60:02d}"
        payload = {
            "employee_id": str(employee_id), "department_id": "", "shift_date": SHIFT_DAY,
            "start_time": start, "end_time": "15:00", "shift_type": "Ranná",
        }
        token = csrf_from(client.get("/shifts/add").get_data(as_text=True))
        payload["csrf_token"] = token

        barrier.wait(timeout=5)
        results[username] = client.post("/shifts/add", data=payload).status_code

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(shifts_module, "validate_shift", slow_validate)

        threads = [
            threading.Thread(target=worker, args=("mgr1", 0)),
            threading.Thread(target=worker, args=("mgr2", 1)),
        ]
        [t.start() for t in threads]

        for thread in threads:
            thread.join(timeout=15)
            assert not thread.is_alive(), "vlákno sa zaseklo"

    rows = shifts_in_db(pg_app)
    assert len(rows) == 1, "presne jedna z dvoch kolidujúcich požiadaviek smie uspieť"
    assert sorted(results.values()) == [200, 302]


def test_lock_employee_for_write_is_visible_as_a_real_advisory_lock(pg_app):
    """Priamy dôkaz, že appka skutočne drží PostgreSQL advisory lock (nie
    len nejaké Python-level zamykanie bez vplyvu na databázu)."""

    from app.extensions import db
    from sqlalchemy import text

    from app.services.concurrency import lock_employee_for_write

    employee_id = pg_app.test_ids["employee_id"]
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with pg_app.app_context():
            with lock_employee_for_write(employee_id):
                holder_ready.set()
                release.wait(timeout=5)
            db.session.rollback()

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(timeout=5)

    try:
        with pg_app.app_context():
            # Iné (nové) pripojenie k tej istej databáze vidí zámok ako
            # aktívny - presne jeden advisory lock, s ID zamestnanca.
            locked = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = :id AND granted"
                ),
                {"id": employee_id},
            ).scalar()
            db.session.rollback()
    finally:
        release.set()
        thread.join(timeout=5)

    assert locked == 1


def test_lock_is_released_when_the_transaction_ends(pg_app):
    from app.extensions import db
    from sqlalchemy import text

    from app.services.concurrency import lock_employee_for_write

    employee_id = pg_app.test_ids["employee_id"]

    with pg_app.app_context():
        with lock_employee_for_write(employee_id):
            pass

        db.session.commit()

        remaining = db.session.execute(
            text(
                "SELECT COUNT(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND objid = :id AND granted"
            ),
            {"id": employee_id},
        ).scalar()
        db.session.rollback()

    assert remaining == 0


def test_lock_does_not_block_a_different_employee(pg_app):
    """Advisory lock je na ID zamestnanca - iný zamestnanec sa nečaká."""

    from app.extensions import db
    from app.services.concurrency import lock_employee_for_write
    from app.services.employee_service import add_employee

    with pg_app.app_context():
        other_id = add_employee("Boris", "Druhý", "Op", 40, pg_app.test_ids["branch_id"])
        db.session.commit()

    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with pg_app.app_context():
            with lock_employee_for_write(pg_app.test_ids["employee_id"]):
                holder_ready.set()
                release.wait(timeout=5)
            db.session.rollback()

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(timeout=5)

    try:
        with pg_app.app_context():
            started = time.monotonic()
            with lock_employee_for_write(other_id):
                pass
            elapsed = time.monotonic() - started
            db.session.rollback()
    finally:
        release.set()
        thread.join(timeout=5)

    assert elapsed < 1, "iný zamestnanec sa nesmie čakať na cudzí zámok"
