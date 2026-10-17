"""Testy nasadenia: výber databázy, produkčný server, proxy/HTTPS,
bezpečnostné hlavičky."""

import sys

import pytest


# ---------------------------------------------------------------------
# Výber databázy
# ---------------------------------------------------------------------

def test_database_uri_priority(monkeypatch, tmp_path):
    from app.data.database import DEFAULT_DATABASE_PATH, resolve_database_uri

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert resolve_database_uri() == f"sqlite:///{DEFAULT_DATABASE_PATH}"

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/plan")
    assert resolve_database_uri() == "postgresql+psycopg2://u:p@db:5432/plan"

    # Explicitná cesta (testy) má prednosť pred DATABASE_URL.
    explicit = tmp_path / "x.db"
    assert resolve_database_uri(explicit) == f"sqlite:///{explicit}"


@pytest.mark.parametrize(
    "given, expected",
    [
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("  postgresql://u:p@h/db  ", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_normalize_database_url(given, expected):
    from app.data.database import normalize_database_url

    assert normalize_database_url(given) == expected


def test_redact_database_uri_hides_password():
    from app.data.database import redact_database_uri

    text = redact_database_uri("postgresql+psycopg2://planner:tajne@db:5432/plan")

    assert "tajne" not in text
    assert "planner" in text and "db:5432/plan" in text


@pytest.mark.sqlite_only
def test_app_uses_database_url_from_environment(monkeypatch):
    from app.web.app import create_app

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/plan")

    app = create_app()

    assert (
        app.config["SQLALCHEMY_DATABASE_URI"]
        == "postgresql+psycopg2://u:p@localhost:5432/plan"
    )
    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"] == {"pool_pre_ping": True}


# ---------------------------------------------------------------------
# Proxy, HTTPS, hlavičky
# ---------------------------------------------------------------------

@pytest.fixture
def make_app(tmp_path):
    from app.web.app import create_app

    def factory():
        app = create_app(database_path=tmp_path / "deploy.db")
        app.config.update(TESTING=True)

        return app

    return factory


def test_proxy_headers_ignored_without_behind_proxy(make_app, monkeypatch):
    from flask import request

    monkeypatch.delenv("BEHIND_PROXY", raising=False)

    app = make_app()

    with app.test_client() as client:
        client.get(
            "/login",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "203.0.113.7",
            },
        )

        assert request.is_secure is False
        assert request.remote_addr != "203.0.113.7"


def test_proxy_headers_trusted_when_behind_proxy(make_app, monkeypatch):
    from flask import request

    monkeypatch.setenv("BEHIND_PROXY", "1")

    app = make_app()

    with app.test_client() as client:
        client.get(
            "/login",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "203.0.113.7",
            },
        )

        assert request.is_secure is True
        assert request.remote_addr == "203.0.113.7"


def test_security_headers_always_present(make_app, monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)

    response = make_app().test_client().get("/login")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in response.headers
    # Bez HTTPS režimu sa HSTS neposiela.
    assert "Strict-Transport-Security" not in response.headers


def test_https_mode_sets_secure_cookie_and_hsts(make_app, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")

    response = make_app().test_client().get("/login")

    assert "Strict-Transport-Security" in response.headers

    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


# ---------------------------------------------------------------------
# Produkčný server (manage.py serve)
# ---------------------------------------------------------------------

def _run_manage(monkeypatch, *args):
    import manage

    monkeypatch.setattr(sys, "argv", ["manage.py", *args])
    manage.main()


def test_serve_refuses_default_secret_key(monkeypatch, capsys):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        _run_manage(monkeypatch, "serve")

    assert exit_info.value.code == 1
    assert "SECRET_KEY" in capsys.readouterr().out


def test_serve_refuses_documented_placeholder_default(monkeypatch):
    from app.web.app import _DEFAULT_SECRET_KEY

    monkeypatch.setenv("SECRET_KEY", _DEFAULT_SECRET_KEY)

    with pytest.raises(SystemExit):
        _run_manage(monkeypatch, "serve")


def test_serve_starts_waitress_with_safe_defaults(monkeypatch):
    import waitress

    calls = {}

    def fake_serve(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(waitress, "serve", fake_serve)
    monkeypatch.setenv("SECRET_KEY", "x" * 64)

    for name in ("HOST", "PORT", "THREADS", "BEHIND_PROXY"):
        monkeypatch.delenv(name, raising=False)

    _run_manage(monkeypatch, "serve")

    # Predvolene len localhost (verejný prístup rieši reverzný proxy).
    assert calls["kwargs"] == {"host": "127.0.0.1", "port": 8000, "threads": 8}
    assert calls["app"].secret_key == "x" * 64


def test_serve_respects_host_port_threads(monkeypatch):
    import waitress

    calls = {}
    monkeypatch.setattr(
        waitress, "serve", lambda app, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("THREADS", "4")
    monkeypatch.setenv("BEHIND_PROXY", "1")

    _run_manage(monkeypatch, "serve")

    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9000
    assert calls["threads"] == 4


def test_serve_trusts_forwarded_headers_only_from_trusted_proxy(monkeypatch):
    """Za proxy musí waitress prijať X-Forwarded-* (inak by všetci
    klienti mali jednu IP a limit prihlasovania by blokoval všetkých),
    ale len od dôveryhodnej adresy proxy."""

    import waitress

    calls = {}
    monkeypatch.setattr(
        waitress, "serve", lambda app, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("SECRET_KEY", "z" * 64)
    monkeypatch.setenv("BEHIND_PROXY", "1")
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)

    _run_manage(monkeypatch, "serve")

    assert calls["trusted_proxy"] == "127.0.0.1"
    assert calls["trusted_proxy_count"] == 1
    assert calls["trusted_proxy_headers"] == {
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
    }

    calls.clear()
    monkeypatch.setenv("TRUSTED_PROXY", "10.0.0.5")

    _run_manage(monkeypatch, "serve")

    assert calls["trusted_proxy"] == "10.0.0.5"


def test_serve_without_proxy_does_not_trust_forwarded_headers(monkeypatch):
    import waitress

    calls = {}
    monkeypatch.setattr(
        waitress, "serve", lambda app, **kwargs: calls.update(kwargs)
    )
    monkeypatch.setenv("SECRET_KEY", "w" * 64)
    monkeypatch.delenv("BEHIND_PROXY", raising=False)

    _run_manage(monkeypatch, "serve")

    assert "trusted_proxy" not in calls
