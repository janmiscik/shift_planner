"""Migrácia databázy pre oddelenia."""

import sqlite3

from app.data.database import DATABASE_PATH


def migrate():
    """Pridá podporu oddelení do existujúcej databázy."""

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    # Zistíme, či už existuje tabuľka departments.
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = 'departments'
        """
    )

    departments_exists = cursor.fetchone()

    if not departments_exists:
        cursor.execute(
            """
            CREATE TABLE departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )

    # Zistíme stĺpce tabuľky employees.
    cursor.execute("PRAGMA table_info(employees)")
    employee_columns = [
        column[1]
        for column in cursor.fetchall()
    ]

    if "department_id" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN department_id INTEGER
            """
        )

    # Zistíme stĺpce tabuľky shifts.
    cursor.execute("PRAGMA table_info(shifts)")
    shift_columns = [
        column[1]
        for column in cursor.fetchall()
    ]

    if "department_id" not in shift_columns:
        cursor.execute(
            """
            ALTER TABLE shifts
            ADD COLUMN department_id INTEGER
            """
        )

    connection.commit()
    connection.close()

    print("Migrácia databázy dokončená.")


if __name__ == "__main__":
    migrate()
