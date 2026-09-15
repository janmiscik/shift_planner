"""Práca s databázou Shift Planner."""

import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).parent / "shift_planner.db"


def get_connection():
    """Vytvorí pripojenie k databáze."""
    return sqlite3.connect(DATABASE_PATH)


def create_tables():
    """Vytvorí potrebné tabuľky."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            position TEXT NOT NULL,
            employment_type TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            department_id INTEGER,
            FOREIGN KEY (department_id)
                REFERENCES departments(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            shift_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            shift_type TEXT NOT NULL,
            department_id INTEGER,
            FOREIGN KEY (employee_id)
                REFERENCES employees(id),
            FOREIGN KEY (department_id)
                REFERENCES departments(id)
        )
        """
    )

    connection.commit()
    connection.close()
