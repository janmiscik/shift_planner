"""Migrácia týždenného pracovného fondu zamestnancov."""

from app.data.database import get_connection


def migrate():
    """Pridá weekly_hours do tabuľky employees."""

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        PRAGMA table_info(employees)
        """
    )

    columns = cursor.fetchall()

    column_names = [
        column[1]
        for column in columns
    ]

    if "weekly_hours" not in column_names:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN weekly_hours REAL
            """
        )

    connection.commit()
    connection.close()


if __name__ == "__main__":
    migrate()
    print("Migrácia weekly_hours dokončená.")