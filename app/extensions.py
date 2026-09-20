"""Zdieľané rozšírenia (SQLAlchemy, Alembic/Flask-Migrate).

Vlastný modul kvôli cyklickým importom - modely aj app.py si `db`
berú odtiaľto, žiadny z nich nezávisí na tom druhom.

``load_dotenv()`` sa volá práve TU (nie len v ``app/web/app.py``),
pretože tento modul si berie ako prvý každý service súbor (napr.
``shift_service.py`` číta ``MIN_REST_HOURS`` z prostredia hneď pri
importe) - takto je zaručené, že sa `.env` načíta VŽDY skôr, než ho
niekto potrebuje, bez ohľadu na to, v akom poradí sa moduly
v konkrétnom vstupnom bode (``main.py``, ``manage.py``, testy...)
importujú.
"""

import sqlite3

from dotenv import load_dotenv
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

load_dotenv()

db = SQLAlchemy()
migrate = Migrate()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """Zapne vynucovanie cudzích kľúčov pre KAŽDÉ SQLite pripojenie
    vytvorené cez SQLAlchemy (aplikácia aj testy) - bez toho by sa
    väzby medzi zamestnancami/oddeleniami/smenami nevynucovali pri
    mazaní."""

    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()
