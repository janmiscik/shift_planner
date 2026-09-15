"""Migrácia rozdelenia pracovného úväzku podľa hodín."""

import sqlite3

from app.data.database import DATABASE_PATH


def migrate():
    """Zmení percentuálny úväzok na počet hodín za týždeň."""

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE employee_departments_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            department_id INTEGER NOT NULL,
            weekly_hours REAL NOT NULL,
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

    cursor.execute(
        """
        INSERT INTO employee_departments_new (
            id,
            employee_id,
            department_id,
            weekly_hours
        )
        SELECT
            id,
            employee_id,
            department_id,
            employment_percentage
        FROM employee_departments
        """
    )

    cursor.execute(
        """
        DROP TABLE employee_departments
        """
    )

    cursor.execute(
        """
        ALTER TABLE employee_departments_new
        RENAME TO employee_departments
        """
    )

    connection.commit()
    connection.close()

    print(
        "Migrácia pracovných hodín bola úspešná."
    )


if __name__ == "__main__":
    migrate()
