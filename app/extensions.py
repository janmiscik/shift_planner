"""Zdieľané rozšírenia (SQLAlchemy, Alembic/Flask-Migrate).

Vlastný modul kvôli cyklickým importom - modely aj app.py si `db`
berú odtiaľto, žiadny z nich nezávisí na tom druhom.
"""

import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

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
