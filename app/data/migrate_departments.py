"""Migrácia databázy pre priradenie zamestnancov k oddeleniam."""

import sqlite3

from app.data.database import DATABASE_PATH


def migrate():
    """Vytvorí tabuľku pre priradenie zamestnancov k oddeleniam."""

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            department_id INTEGER NOT NULL,
            employment_percentage INTEGER NOT NULL,
            FOREIGN KEY (employee_id)
                REFERENCES employees(id),
            FOREIGN KEY (department_id)
                REFERENCES departments(id),
            UNIQUE (
                employee_id,
                department_id
            )
        )
        """
    )

    connection.commit()
    connection.close()

    print(
        "Tabuľka employee_departments je pripravená."
    )


if __name__ == "__main__":
    migrate()

