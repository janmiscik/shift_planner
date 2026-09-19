"""Jednotná správa databázových migrácií.

Namiesto niekoľkých samostatných skriptov (migrate.py,
migrate_departments.py, ...) je tu jeden zoznam migrácií, ktoré sa
aplikujú postupne a evidujú sa v tabuľke ``schema_migrations``, takže
každá migrácia sa spustí presne raz - aj na starej, aj na úplne
novej databáze.

Spustenie:
    python manage.py migrate
"""

import sqlite3

from app.data.database import DATABASE_PATH, get_connection


def _column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(cursor, table):
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table,),
    )
    return cursor.fetchone() is not None


def _migration_001_initial_schema(cursor):
    """Vytvorí základné tabuľky (zamestnanci, smeny)."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            position TEXT NOT NULL,
            employment_type TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
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
            FOREIGN KEY (employee_id) REFERENCES employees(id)
                ON DELETE CASCADE
        )
        """
    )


def _migration_002_departments(cursor):
    """Pridá oddelenia a väzbu na zamestnancov/smeny."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    if not _column_exists(cursor, "employees", "department_id"):
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN department_id INTEGER"
        )

    if not _column_exists(cursor, "shifts", "department_id"):
        cursor.execute(
            "ALTER TABLE shifts ADD COLUMN department_id INTEGER"
        )


def _migration_003_employee_departments(cursor):
    """Vytvorí tabuľku priradení zamestnanec <-> oddelenie (v %)."""

    if not _table_exists(cursor, "employee_departments"):
        cursor.execute(
            """
            CREATE TABLE employee_departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                department_id INTEGER NOT NULL,
                employment_percentage INTEGER NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (department_id) REFERENCES departments(id)
                    ON DELETE RESTRICT,
                UNIQUE (employee_id, department_id)
            )
            """
        )


def _migration_004_weekly_hours(cursor):
    """Prevedie percentuálny úväzok priradení na hodiny za týždeň."""

    if _table_exists(cursor, "employee_departments") and not _column_exists(
        cursor, "employee_departments", "weekly_hours"
    ):
        cursor.execute(
            """
            CREATE TABLE employee_departments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                department_id INTEGER NOT NULL,
                weekly_hours REAL NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (department_id) REFERENCES departments(id)
                    ON DELETE RESTRICT,
                UNIQUE (employee_id, department_id)
            )
            """
        )

        cursor.execute(
            """
            INSERT INTO employee_departments_new (
                id, employee_id, department_id, weekly_hours
            )
            SELECT id, employee_id, department_id, employment_percentage
            FROM employee_departments
            """
        )

        cursor.execute("DROP TABLE employee_departments")
        cursor.execute(
            "ALTER TABLE employee_departments_new "
            "RENAME TO employee_departments"
        )


def _migration_005_employee_weekly_hours(cursor):
    """Pridá celkový týždenný fond hodín priamo na zamestnanca."""

    if not _column_exists(cursor, "employees", "weekly_hours"):
        cursor.execute(
            "ALTER TABLE employees ADD COLUMN weekly_hours REAL"
        )


def _migration_006_shift_created_at(cursor):
    """Pridá časovú značku vytvorenia smeny (pre budúcu dohľadateľnosť
    - kedy a odkiaľ záznam vznikol)."""

    if not _column_exists(cursor, "shifts", "created_at"):
        cursor.execute(
            "ALTER TABLE shifts ADD COLUMN created_at TEXT"
        )

        # Existujúce riadky nemajú známy čas vzniku - označíme ich
        # explicitne, aby sa nedali zamieňať s novými.
        cursor.execute(
            """
            UPDATE shifts
            SET created_at = 'neznámy (pred zavedením sledovania)'
            WHERE created_at IS NULL
            """
        )


def _migration_007_indexes(cursor):
    """Pridá indexy pre najčastejšie dotazy (kalendár, kolízie,
    týždenný fond hodín), aby sa pri väčšom počte smien nespomaľovali.
    """

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shifts_employee_date
        ON shifts (employee_id, shift_date)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shifts_date
        ON shifts (shift_date)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_employee_departments_employee
        ON employee_departments (employee_id)
        """
    )


MIGRATIONS = [
    ("001_initial_schema", _migration_001_initial_schema),
    ("002_departments", _migration_002_departments),
    ("003_employee_departments", _migration_003_employee_departments),
    ("004_weekly_hours", _migration_004_weekly_hours),
    ("005_employee_weekly_hours", _migration_005_employee_weekly_hours),
    ("006_shift_created_at", _migration_006_shift_created_at),
    ("007_indexes", _migration_007_indexes),
]


def _ensure_migrations_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _applied_migrations(cursor):
    cursor.execute("SELECT id FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def run_migrations(verbose=True):
    """Aplikuje všetky migrácie, ktoré ešte neboli spustené."""

    connection = get_connection()
    cursor = connection.cursor()

    _ensure_migrations_table(cursor)
    applied = _applied_migrations(cursor)

    applied_now = []

    for migration_id, migration_fn in MIGRATIONS:
        if migration_id in applied:
            continue

        migration_fn(cursor)

        cursor.execute(
            "INSERT INTO schema_migrations (id) VALUES (?)",
            (migration_id,),
        )

        applied_now.append(migration_id)

    connection.commit()
    connection.close()

    if verbose:
        if applied_now:
            print("Aplikované migrácie:")
            for migration_id in applied_now:
                print(f"  - {migration_id}")
        else:
            print("Databáza je aktuálna, žiadne nové migrácie.")

    return applied_now


if __name__ == "__main__":
    run_migrations()
