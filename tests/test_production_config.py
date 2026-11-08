"""Testy produkčnej konfigurácie: slabý SECRET_KEY, cookie a proxy."""

import re
import secrets
import sys
from pathlib import Path

import pytest

from app.web.config_check import (
    DEFAULT_SECRET_KEY,
    ConfigError,
    explain,
    is_production,
    production_problems,
    validate_production_config,
    weak_secret_reason,
)

STRONG = secrets.token_hex(32)


# ---------------------------------------------------------------------
# Sila kľúča
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "key, expected",
    [
        (None, "nie je nastavený"),
        ("", "nie je nastavený"),
        ("   ", "nie je nastavený"),
        (DEFAULT_SECRET_KEY, "predvolená alebo ukážková"),
        (DEFAULT_SECRET_KEY.upper(), "predvolená alebo ukážková"),
        ("sem-vloz-vygenerovany-nahodny-retazec", "predvolená alebo ukážková"),
        ("changeme", "predvolená alebo ukážková"),
        ("kratky-kluc", "príliš krátky"),
        ("a" * 31 + "b", "príliš krátky"),                 # 32 znakov, ale...
        ("x" * 64, "nevyzerá náhodne"),
        ("ab" * 32, "nevyzerá náhodne"),
        ("0123456789" * 4, "nevyzerá náhodne"),           # 10 rôznych znakov je hranica
    ],
)
def test_weak_secret_reasons(key, expected):
    reason = weak_secret_reason(key)

    if key == "a" * 31 + "b":
        assert reason is not None                          # (len 32, málo rôznych znakov)
    elif key == "0123456789" * 4:
        assert reason is None                              # presne 10 rôznych znakov je ešte prijateľné
    else:
        assert reason and expected in reason


@pytest.mark.parametrize("key", [secrets.token_hex(32), secrets.token_urlsafe(48), secrets.token_hex(16)])
def test_generated_keys_are_accepted(key):
    assert weak_secret_reason(key) is None


def test_the_example_env_file_cannot_be_used_as_is():
    """Ak niekto skopíruje .env.example do produkcie bez zmeny kľúča, appka
    sa nespustí."""

    text = Path(".env.example").read_text(encoding="utf-8")
    match = re.search(r"^SECRET_KEY=(.*)$", text, re.M)

    assert match, ".env.example nemá riadok SECRET_KEY="
    assert weak_secret_reason(match.group(1).strip()) is not None


# ---------------------------------------------------------------------
# Produkčné problémy
# ---------------------------------------------------------------------

def env(**values):
    base = {"SECRET_KEY": STRONG}
    base.update(values)

    return base


def test_a_good_production_configuration_has_no_problems():
    assert production_problems(env()) == []
    assert production_problems(env(BEHIND_PROXY="1", SESSION_COOKIE_SECURE="1"), check_listening=True) == []
    assert production_problems(env(HOST="127.0.0.1"), check_listening=True) == []


@pytest.mark.parametrize(
    "values, fragment",
    [
        ({"SECRET_KEY": "x" * 64}, "SECRET_KEY"),
        ({"SECRET_KEY": ""}, "SECRET_KEY nie je nastavený"),
        ({"BEHIND_PROXY": "1"}, "SESSION_COOKIE_SECURE"),
        ({"BEHIND_PROXY": "1", "SESSION_COOKIE_SECURE": "0"}, "SESSION_COOKIE_SECURE"),
        ({"TRUSTED_PROXY": "*"}, "TRUSTED_PROXY=*"),
    ],
)
def test_production_problems_are_reported(values, fragment):
    problems = production_problems(env(**values))

    assert any(fragment in problem for problem in problems), problems


def test_listening_on_the_network_without_https_is_a_problem_only_for_serve():
    exposed = env(HOST="0.0.0.0")

    assert production_problems(exposed) == []                                   # create_app to neposudzuje
    problems = production_problems(exposed, check_listening=True)
    assert len(problems) == 1 and "0.0.0.0" in problems[0] and "bez HTTPS" in problems[0]

    # Za HTTPS proxy s Secure cookie je to v poriadku.
    assert production_problems(env(HOST="0.0.0.0", BEHIND_PROXY="1", SESSION_COOKIE_SECURE="1"), check_listening=True) == []

    # Priame HTTPS (Secure cookie) bez proxy.
    assert production_problems(env(HOST="0.0.0.0", SESSION_COOKIE_SECURE="1"), check_listening=True) == []


def test_insecure_http_can_be_allowed_explicitly():
    values = env(HOST="0.0.0.0", BEHIND_PROXY="1", ALLOW_INSECURE_HTTP="1")

    assert production_problems(values, check_listening=True) == []

    # Slabý kľúč sa nedá "povoliť".
    assert production_problems(env(SECRET_KEY="abc", ALLOW_INSECURE_HTTP="1")) != []


def test_all_problems_are_listed_together_with_the_fix():
    problems = production_problems(
        env(SECRET_KEY="abc", BEHIND_PROXY="1", TRUSTED_PROXY="*"), check_listening=True
    )
    text = explain(problems)

    assert len(problems) == 3
    for fragment in ("SECRET_KEY", "SESSION_COOKIE_SECURE", "TRUSTED_PROXY=*", "token_hex(32)", "ALLOW_INSECURE_HTTP=1"):
        assert fragment in text


def test_validate_raises_a_config_error():
    with pytest.raises(ConfigError) as error:
        validate_production_config(env(SECRET_KEY="abc"))

    assert isinstance(error.value, RuntimeError) and "SECRET_KEY" in str(error.value)
    validate_production_config(env())                                             # v poriadku = nič nevyhodí


@pytest.mark.parametrize("value, expected", [("production", True), ("Production", True), (" PRODUCTION ", True),
                                             ("development", False), ("", False), ("prod", False)])
def test_is_production(value, expected):
    assert is_production({"APP_ENV": value}) is expected


# ---------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------

def clean_env(monkeypatch, **values):
    for name in ("APP_ENV", "SECRET_KEY", "BEHIND_PROXY", "SESSION_COOKIE_SECURE", "TRUSTED_PROXY",
                 "ALLOW_INSECURE_HTTP", "HOST"):
        monkeypatch.delenv(name, raising=False)

    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_app_refuses_to_start_with_a_weak_secret_key(monkeypatch):
    from app.web.app import create_app

    for key in (None, DEFAULT_SECRET_KEY, "sem-vloz-vygenerovany-nahodny-retazec", "x" * 64, "kratky"):
        values = {"APP_ENV": "production"}

        if key is not None:
            values["SECRET_KEY"] = key

        clean_env(monkeypatch, **values)

        with pytest.raises(ConfigError, match="SECRET_KEY"):
            create_app()


def test_production_app_refuses_secure_cookie_misconfiguration(monkeypatch):
    from app.web.app import create_app

    clean_env(monkeypatch, APP_ENV="production", SECRET_KEY=STRONG, BEHIND_PROXY="1")

    with pytest.raises(ConfigError, match="SESSION_COOKIE_SECURE"):
        create_app()


def test_production_app_starts_with_a_good_configuration(monkeypatch, tmp_path):
    from app.web.app import create_app

    clean_env(monkeypatch, APP_ENV="production", SECRET_KEY=STRONG,
              BEHIND_PROXY="1", SESSION_COOKIE_SECURE="1")

    app = create_app(database_path=tmp_path / "prod.db")

    assert app.secret_key == STRONG
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True and app.config["REMEMBER_COOKIE_HTTPONLY"] is True


def test_the_production_flag_can_be_forced_regardless_of_the_environment(monkeypatch):
    from app.web.app import create_app

    clean_env(monkeypatch, SECRET_KEY="abc")

    with pytest.raises(ConfigError):
        create_app(production=True)

    clean_env(monkeypatch, APP_ENV="production", SECRET_KEY="abc")
    create_app(production=False)                                 # výslovne vypnuté (napr. testy)


def test_development_only_warns(monkeypatch, capsys, tmp_path):
    from app.web.app import create_app

    clean_env(monkeypatch)
    create_app(database_path=tmp_path / "dev.db")
    assert "predvoleným (nebezpečným) SECRET_KEY" in capsys.readouterr().out

    clean_env(monkeypatch, SECRET_KEY="kratky")
    create_app(database_path=tmp_path / "dev.db")
    assert "príliš krátky" in capsys.readouterr().out

    clean_env(monkeypatch, SECRET_KEY=STRONG)
    create_app(database_path=tmp_path / "dev.db")
    assert "UPOZORNENIE" not in capsys.readouterr().out


def test_secure_cookie_follows_the_environment(monkeypatch, tmp_path):
    from app.web.app import create_app

    clean_env(monkeypatch)
    assert create_app(database_path=tmp_path / "a.db").config["SESSION_COOKIE_SECURE"] is False

    clean_env(monkeypatch, SESSION_COOKIE_SECURE="1")
    assert create_app(database_path=tmp_path / "b.db").config["SESSION_COOKIE_SECURE"] is True


# ---------------------------------------------------------------------
# manage.py serve
# ---------------------------------------------------------------------

def serve(monkeypatch):
    import manage

    monkeypatch.setattr(sys, "argv", ["manage.py", "serve"])
    manage.main()


@pytest.fixture
def fake_waitress(monkeypatch):
    import waitress

    calls = {}
    monkeypatch.setattr(waitress, "serve", lambda app, **kwargs: calls.update(kwargs, app=app))

    return calls


@pytest.mark.parametrize(
    "values, fragment",
    [
        ({"SECRET_KEY": "x" * 64}, "nevyzerá náhodne"),
        ({"SECRET_KEY": "sem-vloz-vygenerovany-nahodny-retazec"}, "ukážková"),
        ({"SECRET_KEY": "kratky"}, "príliš krátky"),
        ({"BEHIND_PROXY": "1"}, "SESSION_COOKIE_SECURE"),
        ({"HOST": "0.0.0.0"}, "bez HTTPS"),
        ({"BEHIND_PROXY": "1", "SESSION_COOKIE_SECURE": "1", "TRUSTED_PROXY": "*"}, "TRUSTED_PROXY=*"),
    ],
)
def test_serve_refuses_dangerous_configuration(monkeypatch, capsys, fake_waitress, values, fragment):
    clean_env(monkeypatch, SECRET_KEY=STRONG)

    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exit_info:
        serve(monkeypatch)

    assert exit_info.value.code == 1
    assert fragment in capsys.readouterr().out
    assert fake_waitress == {}                                   # server sa nespustil


def test_serve_starts_with_a_good_configuration_and_does_not_leak_production_mode(monkeypatch, fake_waitress):
    clean_env(monkeypatch, SECRET_KEY=STRONG)

    serve(monkeypatch)

    assert fake_waitress["host"] == "127.0.0.1"
    # Produkčný režim platí len pre tento server, neprenáša sa do prostredia.
    import os

    assert "APP_ENV" not in os.environ


def test_serve_allows_an_explicit_insecure_internal_deployment(monkeypatch, fake_waitress):
    clean_env(monkeypatch, SECRET_KEY=STRONG, HOST="0.0.0.0", ALLOW_INSECURE_HTTP="1")

    serve(monkeypatch)

    assert fake_waitress["host"] == "0.0.0.0"


def test_serve_warns_about_a_missing_trusted_proxy(monkeypatch, capsys, fake_waitress):
    clean_env(monkeypatch, SECRET_KEY=STRONG, HOST="0.0.0.0", BEHIND_PROXY="1", SESSION_COOKIE_SECURE="1")

    serve(monkeypatch)

    assert "TRUSTED_PROXY nie je nastavený" in capsys.readouterr().out

    monkeypatch.setenv("TRUSTED_PROXY", "10.0.0.5")
    serve(monkeypatch)
    assert "TRUSTED_PROXY nie je nastavený" not in capsys.readouterr().out
