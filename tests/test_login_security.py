"""Testy ochrany prihlásenia: limit pokusov, zablokovanie, časovanie."""

import re

import pytest

from app.services.login_throttle import LoginThrottle


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------
# LoginThrottle samotný (s ovládaným časom)
# ---------------------------------------------------------------------

@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def throttle(clock):
    return LoginThrottle(
        max_attempts=5,
        max_attempts_per_ip=20,
        lockout_seconds=900,
        clock=clock,
    )


def test_not_blocked_before_limit(throttle):
    for _ in range(4):
        throttle.record_failure("1.1.1.1", "anna")

    assert throttle.retry_after("1.1.1.1", "anna") == 0


def test_blocked_after_limit_and_counts_down(throttle, clock):
    for _ in range(5):
        throttle.record_failure("1.1.1.1", "anna")

    assert throttle.retry_after("1.1.1.1", "anna") == 900

    clock.advance(600)
    assert throttle.retry_after("1.1.1.1", "anna") == 300

    clock.advance(300)
    assert throttle.retry_after("1.1.1.1", "anna") == 0


def test_lockout_does_not_extend_by_checking(throttle, clock):
    for _ in range(5):
        throttle.record_failure("1.1.1.1", "anna")

    for _ in range(10):
        clock.advance(10)
        throttle.retry_after("1.1.1.1", "anna")

    assert throttle.retry_after("1.1.1.1", "anna") == 800


def test_failures_expire_outside_window(throttle, clock):
    for _ in range(4):
        throttle.record_failure("1.1.1.1", "anna")

    clock.advance(901)
    throttle.record_failure("1.1.1.1", "anna")

    # Staré pokusy vypršali - ide len o 1. pokus, nie o 5.
    assert throttle.retry_after("1.1.1.1", "anna") == 0


def test_other_ip_and_other_username_are_independent(throttle):
    for _ in range(5):
        throttle.record_failure("1.1.1.1", "anna")

    assert throttle.retry_after("1.1.1.1", "anna") > 0
    assert throttle.retry_after("2.2.2.2", "anna") == 0
    assert throttle.retry_after("1.1.1.1", "boris") == 0


def test_success_resets_pair_counter(throttle):
    for _ in range(4):
        throttle.record_failure("1.1.1.1", "anna")

    throttle.record_success("1.1.1.1", "anna")

    for _ in range(4):
        throttle.record_failure("1.1.1.1", "anna")

    assert throttle.retry_after("1.1.1.1", "anna") == 0


def test_ip_limit_blocks_many_usernames_from_same_ip(throttle):
    # 20 pokusov s rôznymi menami - žiadne meno samo nedosiahne limit 5.
    for number in range(20):
        throttle.record_failure("1.1.1.1", f"meno{number}")

    assert throttle.retry_after("1.1.1.1", "uplne-nove-meno") > 0
    assert throttle.retry_after("2.2.2.2", "uplne-nove-meno") == 0


def test_success_does_not_clear_ip_counter(throttle):
    for number in range(19):
        throttle.record_failure("1.1.1.1", f"meno{number}")

    throttle.record_success("1.1.1.1", "meno0")
    throttle.record_failure("1.1.1.1", "dalsie")

    assert throttle.retry_after("1.1.1.1", "ktorekolvek") > 0


def test_cleanup_removes_expired_entries(clock, monkeypatch):
    import app.services.login_throttle as module

    monkeypatch.setattr(module, "_CLEANUP_THRESHOLD", 50)
    small = LoginThrottle(lockout_seconds=900, clock=clock)

    for number in range(60):
        small.record_failure("1.1.1.1", f"meno{number}")

    clock.advance(1000)
    small.record_failure("1.1.1.1", "nove")

    assert len(small._failures) < 10


def test_from_env_reads_limits(monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", "2")

    throttle = LoginThrottle.from_env()

    assert throttle.max_attempts == 3
    assert throttle.max_attempts_per_ip == 7
    assert throttle.lockout_seconds == 120


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
def test_from_env_falls_back_to_defaults_on_bad_values(monkeypatch, bad):
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", bad)
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", bad)

    throttle = LoginThrottle.from_env()

    assert throttle.max_attempts == 5
    assert throttle.lockout_seconds == 900


# ---------------------------------------------------------------------
# Prihlasovanie cez appku
# ---------------------------------------------------------------------

def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture
def secured_app(tmp_path):
    from app.extensions import db
    from app.services.auth_service import create_admin
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "sec.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    with app.app_context():
        db.create_all()
        create_admin("admin", "adminpass1")
        create_admin("druhy", "druhypass1")

    clock = FakeClock()
    app.extensions["login_throttle"] = LoginThrottle(
        max_attempts=5, max_attempts_per_ip=20, lockout_seconds=900, clock=clock
    )
    app.test_clock = clock

    return app


def try_login(app, username, password, ip="10.0.0.1"):
    client = app.test_client()
    token = _csrf(client.get("/login", environ_base={"REMOTE_ADDR": ip}).get_data(as_text=True))

    response = client.post(
        "/login",
        data={"csrf_token": token, "username": username, "password": password},
        environ_base={"REMOTE_ADDR": ip},
    )

    return client, response


def test_account_is_blocked_after_five_wrong_passwords(secured_app):
    for _ in range(5):
        _, response = try_login(secured_app, "admin", "zle")
        assert response.status_code == 200
        assert "Nesprávne" in response.get_data(as_text=True)

    # Šiesty pokus - aj so SPRÁVNYM heslom - je zablokovaný.
    _, response = try_login(secured_app, "admin", "adminpass1")

    assert response.status_code == 429
    text = response.get_data(as_text=True)
    assert "Príliš veľa" in text
    assert "15 min" in text


def test_lockout_expires_with_time(secured_app):
    for _ in range(5):
        try_login(secured_app, "admin", "zle")

    assert try_login(secured_app, "admin", "adminpass1")[1].status_code == 429

    secured_app.test_clock.advance(901)

    client, response = try_login(secured_app, "admin", "adminpass1")
    assert response.status_code == 302
    assert client.get("/").status_code == 200


def test_attacker_ip_cannot_lock_out_real_user_elsewhere(secured_app):
    for _ in range(5):
        try_login(secured_app, "admin", "zle", ip="203.0.113.50")

    assert (
        try_login(secured_app, "admin", "adminpass1", ip="203.0.113.50")[1]
        .status_code
        == 429
    )

    # Skutočný používateľ z inej adresy sa prihlási bez problémov.
    _, response = try_login(secured_app, "admin", "adminpass1", ip="198.51.100.7")
    assert response.status_code == 302


def test_other_account_from_same_ip_still_works(secured_app):
    for _ in range(5):
        try_login(secured_app, "admin", "zle")

    _, response = try_login(secured_app, "druhy", "druhypass1")
    assert response.status_code == 302


def test_successful_login_resets_failed_attempts(secured_app):
    for _ in range(4):
        try_login(secured_app, "admin", "zle")

    assert try_login(secured_app, "admin", "adminpass1")[1].status_code == 302

    for _ in range(4):
        try_login(secured_app, "admin", "zle")

    assert try_login(secured_app, "admin", "adminpass1")[1].status_code == 302


def test_unknown_username_is_throttled_like_a_real_one(secured_app):
    """Neexistujúce meno sa správa rovnako - nedá sa zistiť, ktoré
    mená existujú."""

    for _ in range(5):
        _, response = try_login(secured_app, "neexistuje", "zle")
        assert "Nesprávne" in response.get_data(as_text=True)

    _, response = try_login(secured_app, "neexistuje", "zle")

    assert response.status_code == 429


def test_username_case_and_whitespace_do_not_bypass_limit(secured_app):
    for name in ("admin", "ADMIN", " Admin ", "admin", "aDMIN"):
        try_login(secured_app, name, "zle")

    assert try_login(secured_app, "admin", "adminpass1")[1].status_code == 429


def test_failed_attempts_are_logged_without_password(secured_app, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        try_login(secured_app, "admin", "tajne-heslo-123", ip="10.9.8.7")

    assert "Neúspešné prihlásenie" in caplog.text
    assert "10.9.8.7" in caplog.text
    assert "admin" in caplog.text
    assert "tajne-heslo-123" not in caplog.text


def test_failed_login_does_not_log_in(secured_app):
    client, _ = try_login(secured_app, "admin", "zle")

    assert client.get("/").status_code == 302


# ---------------------------------------------------------------------
# Časovanie: neexistujúce meno nesmie byť citeľne rýchlejšie
# ---------------------------------------------------------------------

def test_authenticate_checks_a_hash_even_for_unknown_user(secured_app, monkeypatch):
    import app.services.auth_service as auth_service

    calls = []
    real_check = auth_service.check_password_hash

    def spy(password_hash, password):
        calls.append(password_hash)
        return real_check(password_hash, password)

    monkeypatch.setattr(auth_service, "check_password_hash", spy)

    with secured_app.app_context():
        assert auth_service.authenticate("neexistuje", "heslo") is None
        assert auth_service.authenticate("admin", "zle") is None
        assert auth_service.authenticate("admin", "adminpass1") is not None

    # Pri neexistujúcom mene sa overovalo voči fiktívnemu hashu.
    assert len(calls) == 3
    assert calls[0] == auth_service._DUMMY_PASSWORD_HASH


# ---------------------------------------------------------------------
# Zmena hesla: hádanie aktuálneho hesla je tiež obmedzené
# ---------------------------------------------------------------------

def test_password_change_guessing_is_throttled(secured_app):
    client, response = try_login(secured_app, "admin", "adminpass1")
    assert response.status_code == 302

    def change(current):
        token = _csrf(client.get("/account/password").get_data(as_text=True))

        return client.post(
            "/account/password",
            data={
                "csrf_token": token,
                "current_password": current,
                "new_password": "novyheslo1",
                "confirm_password": "novyheslo1",
            },
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
        )

    for _ in range(5):
        assert "nie je správne" in change("zle").get_data(as_text=True)

    response = change("adminpass1")

    assert response.status_code == 429
    assert "Príliš veľa" in response.get_data(as_text=True)

    # Heslo sa nezmenilo.
    from app.services.auth_service import authenticate

    with secured_app.app_context():
        assert authenticate("admin", "adminpass1") is not None
