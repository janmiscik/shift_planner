import sqlite3
import subprocess

print("Hladam vsetky subory shift_planner*.db na disku C:...")
print("(moze to chvilu trvat)")
print()

result = subprocess.run(
    [
        "powershell",
        "-Command",
        "Get-ChildItem -Path C:\\ -Filter 'shift_planner*.db' "
        "-Recurse -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty FullName",
    ],
    capture_output=True,
    text=True,
)

paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]

if not paths:
    print("Nenasiel som ziadny subor shift_planner*.db na C:\\")
else:
    print(f"Najdenych suborov: {len(paths)}")
    print()

    for path in paths:
        print(f"--- {path} ---")

        try:
            conn = sqlite3.connect(path)

            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]

            if "employees" in tables:
                emp_count = conn.execute(
                    "SELECT COUNT(*) FROM employees"
                ).fetchone()[0]
                shift_count = (
                    conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
                    if "shifts" in tables
                    else "?"
                )
                print(
                    f"  zamestnanci: {emp_count}, smeny: {shift_count}"
                )
            else:
                print("  (nema tabulku employees - iny/stary format)")

            conn.close()
        except Exception as e:
            print(f"  CHYBA pri citani: {e}")

        print()

print("HOTOVO")
