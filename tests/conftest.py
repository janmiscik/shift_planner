"""Spoločné nastavenie testov.

Predvolene každý test beží na vlastnej dočasnej SQLite databáze.

Celú sadu testov možno spustiť aj proti PostgreSQL - nastav
``TEST_DATABASE_URL`` na PRÁZDNU testovaciu databázu (POZOR: pred
každým testom sa v nej zmaže všetko!), napr. v PowerShelli:

    $env:TEST_DATABASE_URL = "postgresql://planner:heslo@localhost:5432/shift_planner_test"
    python -m pytest -q

Testy špecifické pre SQLite (súborové zálohy a pod.) sa v tomto režime
preskočia.
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

if TEST_DATABASE_URL:
    import app.web.app as app_module

    _original_create_app = app_module.create_app
    _previous_apps = []

    def _create_app_on_postgres(database_path=None):
        from app.extensions import db

        # Uvoľni spojenia z predchádzajúcich testov (PostgreSQL má
        # limit súbežných pripojení).
        while _previous_apps:
            previous = _previous_apps.pop()

            with previous.app_context():
                db.session.remove()
                db.engine.dispose()

        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

        app = _original_create_app(database_path=None)

        with app.app_context():
            with db.engine.begin() as connection:
                connection.exec_driver_sql("DROP SCHEMA public CASCADE")
                connection.exec_driver_sql("CREATE SCHEMA public")

        _previous_apps.append(app)

        return app

    # Verzia bez mazania databázy - pre testy, ktoré potrebujú, aby sa
    # dáta medzi dvomi vytvoreniami appky zachovali (napr. migrácia).
    app_module._create_app_keep_data = lambda: _original_create_app(
        database_path=None
    )
    app_module.create_app = _create_app_on_postgres


def pytest_collection_modifyitems(config, items):
    if not TEST_DATABASE_URL:
        return

    skip_sqlite_only = pytest.mark.skip(
        reason="test je špecifický pre SQLite (súbor databázy)"
    )

    for item in items:
        if "sqlite_only" in item.keywords:
            item.add_marker(skip_sqlite_only)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sqlite_only: test funguje len na SQLite (preskočí sa pri "
        "TEST_DATABASE_URL)",
    )
