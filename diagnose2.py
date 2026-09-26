import sqlite3

conn = sqlite3.connect("app/data/shift_planner.db")

print("--- Tabulky ---")
tables = [
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
]
print(tables)
print()

for table in ["employees", "shifts", "departments", "users", "absences"]:
    if table not in tables:
        print(f"--- {table}: TABULKA NEEXISTUJE ---")
        continue

    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"--- {table}: {count} riadkov ---")

    if count > 0:
        rows = conn.execute(f"SELECT * FROM {table} LIMIT 5").fetchall()
        for row in rows:
            print(" ", row)

    print()

print("--- Alembic verzia ---")
try:
    print(conn.execute("SELECT * FROM alembic_version").fetchall())
except sqlite3.OperationalError as e:
    print("CHYBA:", e)

conn.close()
