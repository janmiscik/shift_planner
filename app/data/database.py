"""Práca s databázou Shift Planner."""

import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "shift_planner.db"


def get_connection():
    """Vytvorí pripojenie k databáze."""

    connection = sqlite3.connect(DATABASE_PATH)

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def create_tables():
    """Vytvorí/aktualizuje schému databázy.

    Deleguje na jednotný migračný runner (``app.data.migrations``),
    ktorý aplikuje všetky doteraz nespustené migrácie. Import je
    zámerne až vnútri funkcie, aby sa predišlo cyklickému importu
    (migrations.py si z tohto modulu berie ``get_connection``).
    """

    from app.data.migrations import run_migrations

    run_migrations(verbose=False)