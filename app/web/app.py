"""Webová aplikácia Shift Planner.

Táto vrstva sa stará výlučne o spracovanie HTTP požiadaviek
(parsovanie vstupov, presmerovania, JSON odpovede). Biznis logika
a výpočty (kolízie smien, týždenné fondy, kalendárna mriežka,
exporty) žijú v ``app/services``.
"""

import os
from datetime import date

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file
from flask_wtf import CSRFProtect

from app.data.database import create_tables

load_dotenv()

from app.services.employee_service import (
    add_employee,
    get_employee,
    get_employees,
    set_employee_active,
    update_employee,
)

from app.services.department_service import (
    add_department,
    get_department,
    get_departments,
    set_department_active,
    update_department,
)

from app.services.employee_department_service import (
    add_employee_department,
    can_add_employee_department,
    can_update_employee_department,
    delete_employee_department,
    get_employee_department,
    get_employee_department_hours,
    get_employee_department_ids,
    get_employee_departments,
    update_employee_department,
)

from app.services.shift_service import (
    add_shift,
    delete_shift,
    filter_shifts_by_department,
    get_shift,
    get_shifts,
    get_shifts_by_employee,
    get_shifts_in_range,
    move_shift,
    update_shift,
    validate_shift,
)

from app.services.calendar_service import (
    build_calendar_days,
    month_bounds,
    shifts_to_fullcalendar_events,
)

from app.services.export_service import (
    build_shifts_ics,
    build_shifts_pdf,
    build_shifts_workbook,
)

from app.web.forms import (
    DepartmentForm,
    EmployeeDepartmentForm,
    EmployeeForm,
    ShiftForm,
)


app = Flask(__name__)

_DEFAULT_SECRET_KEY = "dev-docasny-kluc-zmen-v-produkcii"

app.secret_key = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)

if app.secret_key == _DEFAULT_SECRET_KEY:
    print(
        "UPOZORNENIE: beží sa s predvoleným (nebezpečným) SECRET_KEY. "
        "Pred nasadením do produkcie nastav premennú prostredia "
        "SECRET_KEY na náhodný, tajný reťazec - napr.:\n"
        "  python -c \"import secrets; print(secrets.token_hex(32))\""
    )

csrf = CSRFProtect(app)


def _active_department_choices():
    return [
        (department[0], department[1])
        for department in get_departments()
        if department[2]
    ]


def _active_employee_choices():
    return [
        (employee[0], f"{employee[1]} {employee[2]} — {employee[3]}")
        for employee in get_employees()
        if employee[5]
    ]


@app.route("/")
def home():
    employees = get_employees()
    shifts = get_shifts()

    return render_template(
        "dashboard.html",
        employees=employees,
        shifts=shifts,
    )


# ---------------------------------------------------------------------
# Zamestnanci
# ---------------------------------------------------------------------

@app.route("/employees")
def employees_page():
    employees = get_employees()

    return render_template("employees.html", employees=employees)


@app.route("/employees/add", methods=["GET", "POST"])
def add_employee_page():
    form = EmployeeForm()

    if form.validate_on_submit():
        add_employee(
            form.first_name.data,
            form.last_name.data,
            form.position.data,
            form.weekly_hours.data,
        )

        return redirect("/employees")

    return render_template("add_employee.html", form=form)


@app.route("/employees/edit/<int:employee_id>", methods=["GET", "POST"])
def edit_employee_page(employee_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    form = EmployeeForm()

    if request.method == "GET":
        form.first_name.data = employee[1]
        form.last_name.data = employee[2]
        form.position.data = employee[3]
        form.weekly_hours.data = employee[4]

    assignments = get_employee_departments(employee_id)
    total_weekly_hours = get_employee_department_hours(employee_id)

    if form.validate_on_submit():
        if form.weekly_hours.data < total_weekly_hours:
            flash(
                "Nový pracovný fond nemôže byť nižší ako počet hodín "
                "pridelených oddeleniam.",
                "error",
            )

            return render_template(
                "edit_employee.html",
                employee=employee,
                assignments=assignments,
                total_weekly_hours=total_weekly_hours,
                form=form,
            )

        update_employee(
            employee_id,
            form.first_name.data,
            form.last_name.data,
            form.position.data,
            form.weekly_hours.data,
        )

        return redirect("/employees")

    return render_template(
        "edit_employee.html",
        employee=employee,
        assignments=assignments,
        total_weekly_hours=total_weekly_hours,
        form=form,
    )


@app.route(
    "/employees/<int:employee_id>/departments/add",
    methods=["GET", "POST"],
)
def add_employee_department_page(employee_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    departments = get_departments()

    form = EmployeeDepartmentForm()
    form.department_id.choices = _active_department_choices()

    if form.validate_on_submit():
        department = get_department(form.department_id.data)

        if department is None or not department[2]:
            flash(
                "Neaktívne oddelenie nie je možné priradiť zamestnancovi.",
                "error",
            )

            return render_template(
                "add_employee_department.html",
                employee=employee,
                departments=departments,
                form=form,
            )

        if not can_add_employee_department(
            employee_id,
            form.weekly_hours.data,
        ):
            flash(
                "Priradenie prekračuje týždenný pracovný fond zamestnanca.",
                "error",
            )

            return render_template(
                "add_employee_department.html",
                employee=employee,
                departments=departments,
                form=form,
            )

        assignment_id = add_employee_department(
            employee_id=employee_id,
            department_id=form.department_id.data,
            weekly_hours=form.weekly_hours.data,
        )

        if assignment_id is None:
            return render_template(
                "employee_department_duplicate.html",
                employee=employee,
            )

        return redirect(f"/employees/edit/{employee_id}")

    return render_template(
        "add_employee_department.html",
        employee=employee,
        departments=departments,
        form=form,
    )


@app.route(
    "/employees/<int:employee_id>/departments/edit/<int:assignment_id>",
    methods=["GET", "POST"],
)
def edit_employee_department_page(employee_id, assignment_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    assignment = get_employee_department(assignment_id)

    if assignment is None:
        return "Priradenie neexistuje.", 404

    if assignment[1] != employee_id:
        return "Priradenie nepatrí tomuto zamestnancovi.", 404

    departments = get_departments()

    form = EmployeeDepartmentForm()
    form.department_id.choices = _active_department_choices()

    if request.method == "GET":
        form.department_id.data = assignment[2]
        form.weekly_hours.data = assignment[3]

    if form.validate_on_submit():
        department = get_department(form.department_id.data)

        if department is None or not department[2]:
            flash(
                "Neaktívne oddelenie nie je možné priradiť zamestnancovi.",
                "error",
            )

            return render_template(
                "edit_employee_department.html",
                employee=employee,
                assignment=assignment,
                departments=departments,
                form=form,
            )

        if not can_update_employee_department(
            employee_id,
            assignment_id,
            form.weekly_hours.data,
        ):
            flash(
                "Úprava prekračuje týždenný pracovný fond zamestnanca.",
                "error",
            )

            return render_template(
                "edit_employee_department.html",
                employee=employee,
                assignment=assignment,
                departments=departments,
                form=form,
            )

        update_employee_department(
            assignment_id=assignment_id,
            department_id=form.department_id.data,
            weekly_hours=form.weekly_hours.data,
        )

        return redirect(f"/employees/edit/{employee_id}")

    return render_template(
        "edit_employee_department.html",
        employee=employee,
        assignment=assignment,
        departments=departments,
        form=form,
    )


@app.route(
    "/employees/<int:employee_id>/departments/delete/<int:assignment_id>",
    methods=["POST"],
)
def delete_employee_department_page(employee_id, assignment_id):
    assignment = get_employee_department(assignment_id)

    if assignment is None:
        return "Priradenie neexistuje.", 404

    if assignment[1] != employee_id:
        return "Priradenie nepatrí tomuto zamestnancovi.", 404

    delete_employee_department(assignment_id)

    return redirect(f"/employees/edit/{employee_id}")


@app.route("/employees/<int:employee_id>/export.ics")
def export_employee_shifts_ics(employee_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    shifts = get_shifts_by_employee(employee_id)
    calendar_name = f"Moje smeny - {employee[1]} {employee[2]}"

    ics = build_shifts_ics(shifts, calendar_name=calendar_name)

    return send_file(
        ics,
        as_attachment=True,
        download_name=f"moje_smeny_{employee[1]}_{employee[2]}.ics",
        mimetype="text/calendar",
    )


@app.route("/employees/toggle/<int:employee_id>", methods=["POST"])
def toggle_employee_page(employee_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    new_status = 0 if employee[5] else 1
    set_employee_active(employee_id, new_status)

    return redirect("/employees")


# ---------------------------------------------------------------------
# Oddelenia
# ---------------------------------------------------------------------

@app.route("/departments")
def departments_page():
    departments = get_departments()

    return render_template("departments.html", departments=departments)


@app.route("/departments/add", methods=["GET", "POST"])
def add_department_page():
    form = DepartmentForm()

    if form.validate_on_submit():
        department_id = add_department(form.name.data)

        if department_id is None:
            flash("Oddelenie s týmto názvom už existuje.", "error")
            return render_template("add_department.html", form=form)

        return redirect("/departments")

    return render_template("add_department.html", form=form)


@app.route("/departments/edit/<int:department_id>", methods=["GET", "POST"])
def edit_department_page(department_id):
    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    form = DepartmentForm()

    if request.method == "GET":
        form.name.data = department[1]

    if form.validate_on_submit():
        updated = update_department(department_id, form.name.data)

        if not updated:
            flash("Oddelenie s týmto názvom už existuje.", "error")

            return render_template(
                "edit_department.html",
                department=department,
                form=form,
            )

        return redirect("/departments")

    return render_template(
        "edit_department.html",
        department=department,
        form=form,
    )


@app.route("/departments/toggle/<int:department_id>", methods=["POST"])
def toggle_department_page(department_id):
    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    new_status = 0 if department[2] else 1
    set_department_active(department_id, new_status)

    return redirect("/departments")


# ---------------------------------------------------------------------
# Smeny
# ---------------------------------------------------------------------

@app.route("/shifts")
def shifts_page():
    shifts = get_shifts()

    return render_template("shifts.html", shifts=shifts)


@app.route("/shifts/export.xlsx")
def export_shifts_xlsx():
    shifts = get_shifts()
    workbook = build_shifts_workbook(shifts, title="Všetky smeny")

    return send_file(
        workbook,
        as_attachment=True,
        download_name="smeny.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


@app.route("/shifts/export.pdf")
def export_shifts_pdf():
    shifts = get_shifts()
    pdf = build_shifts_pdf(shifts, title="Harmonogram smien - všetky")

    return send_file(
        pdf,
        as_attachment=True,
        download_name="smeny.pdf",
        mimetype="application/pdf",
    )


@app.route("/shifts/export.ics")
def export_shifts_ics():
    shifts = get_shifts()
    ics = build_shifts_ics(shifts, calendar_name="Shift Planner - všetky smeny")

    return send_file(
        ics,
        as_attachment=True,
        download_name="smeny.ics",
        mimetype="text/calendar",
    )


@app.route("/shifts/add", methods=["GET", "POST"])
def add_shift_page():
    employees = get_employees()

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices()
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name) for dep_id, name in _active_department_choices()
    ]

    if request.method == "GET":
        selected_date = request.args.get("date", "")

        if selected_date:
            try:
                form.shift_date.data = date.fromisoformat(selected_date)
            except ValueError:
                pass

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=selected_date,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=request.form.get("shift_date", ""),
            form=form,
        )

    employee_id = form.employee_id.data
    shift_date = form.shift_date.data.isoformat()
    start_time = form.start_time.data.strftime("%H:%M")
    end_time = form.end_time.data.strftime("%H:%M")
    shift_type = form.shift_type.data
    department_id = form.department_id.data or None

    employee = get_employee(employee_id)

    if employee is None or not employee[5]:
        flash("Neaktívnemu zamestnancovi nie je možné pridať smenu.", "error")

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=shift_date,
            form=form,
        )

    errors = validate_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        department_id=department_id,
    )

    if errors:
        for error in errors:
            flash(error, "error")

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=shift_date,
            form=form,
        )

    add_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        shift_type,
        department_id=department_id,
    )

    return redirect("/shifts")


@app.route("/shifts/edit/<int:shift_id>", methods=["GET", "POST"])
def edit_shift_page(shift_id):
    shift = get_shift(shift_id)
    employees = get_employees()

    if shift is None:
        return "Smena neexistuje.", 404

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices()
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name) for dep_id, name in _active_department_choices()
    ]

    if request.method == "GET":
        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    employee_id = form.employee_id.data
    shift_date = form.shift_date.data.isoformat()
    start_time = form.start_time.data.strftime("%H:%M")
    end_time = form.end_time.data.strftime("%H:%M")
    shift_type = form.shift_type.data
    department_id = form.department_id.data or None

    errors = validate_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        department_id=department_id,
        exclude_shift_id=shift_id,
    )

    if errors:
        for error in errors:
            flash(error, "error")

        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    update_shift(
        shift_id,
        employee_id,
        shift_date,
        start_time,
        end_time,
        shift_type,
        department_id=department_id,
    )

    return redirect("/shifts")


@app.route("/shifts/delete/<int:shift_id>", methods=["POST"])
def delete_shift_page(shift_id):
    delete_shift(shift_id)

    return redirect("/shifts")


# ---------------------------------------------------------------------
# Kalendár
# ---------------------------------------------------------------------

@app.route("/calendar")
def calendar_page():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    employee_id = request.args.get("employee_id", type=int)
    department_id = request.args.get("department_id", type=int)

    employees = get_employees()
    departments = get_departments()

    calendar_days = build_calendar_days(year, month)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        calendar_days=calendar_days,
        employees=employees,
        departments=departments,
        selected_employee_id=employee_id,
        selected_department_id=department_id,
    )


@app.route("/calendar/export.xlsx")
def export_calendar_xlsx():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    first_day, last_day = month_bounds(year, month)
    shifts = get_shifts_in_range(first_day, last_day)

    workbook = build_shifts_workbook(
        shifts,
        title=f"Smeny {month:02d}-{year}",
    )

    return send_file(
        workbook,
        as_attachment=True,
        download_name=f"smeny_{year}_{month:02d}.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


@app.route("/calendar/export.pdf")
def export_calendar_pdf():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    first_day, last_day = month_bounds(year, month)
    shifts = get_shifts_in_range(first_day, last_day)

    pdf = build_shifts_pdf(
        shifts,
        title=f"Harmonogram smien - {month:02d}/{year}",
    )

    return send_file(
        pdf,
        as_attachment=True,
        download_name=f"smeny_{year}_{month:02d}.pdf",
        mimetype="application/pdf",
    )


@app.route("/calendar/export.ics")
def export_calendar_ics():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    first_day, last_day = month_bounds(year, month)
    shifts = get_shifts_in_range(first_day, last_day)

    ics = build_shifts_ics(
        shifts,
        calendar_name=f"Shift Planner - {month:02d}/{year}",
    )

    return send_file(
        ics,
        as_attachment=True,
        download_name=f"smeny_{year}_{month:02d}.ics",
        mimetype="text/calendar",
    )


# ---------------------------------------------------------------------
# JSON API (AJAX pre formuláre a FullCalendar drag-and-drop)
# ---------------------------------------------------------------------

@app.route("/api/employees/<int:employee_id>/departments")
def api_employee_departments(employee_id):
    assignments = get_employee_departments(employee_id)

    return jsonify(
        [
            {"id": assignment[1], "name": assignment[2]}
            for assignment in assignments
        ]
    )


@app.route("/api/shifts")
def api_shifts():
    """Vráti smeny pre FullCalendar.

    Prijíma buď presný dátumový rozsah (``start``/``end`` ako ISO
    dátumy - presne to, čo posiela FullCalendar pre aktuálne
    zobrazenú mriežku, vrátane dní susedných mesiacov), alebo
    ``year``/``month`` ako jednoduchšiu alternatívu (napr. pre iné
    integrácie). Rozsah má prednosť, ak je zadaný.
    """

    today = date.today()

    start = request.args.get("start")
    end = request.args.get("end")
    employee_id = request.args.get("employee_id", type=int)
    department_id = request.args.get("department_id", type=int)

    if start and end:
        # FullCalendar posiela "end" ako exkluzívny (deň po poslednom
        # zobrazenom dni) - orežeme čas, ak by tam bol.
        first_day = start[:10]
        last_day = end[:10]
    else:
        year = request.args.get("year", today.year, type=int)
        month = request.args.get("month", today.month, type=int)
        first_day, last_day = month_bounds(year, month)

    shifts = get_shifts_in_range(first_day, last_day)

    if employee_id:
        shifts = [
            shift for shift in shifts if shift[7] == employee_id
        ]

    if department_id:
        shifts = filter_shifts_by_department(shifts, department_id)

    return jsonify(shifts_to_fullcalendar_events(shifts))


@app.route("/api/shifts/<int:shift_id>/move", methods=["POST"])
def api_move_shift(shift_id):
    """Presunie smenu po drag-and-drop v kalendári (FullCalendar)."""

    payload = request.get_json(silent=True) or {}

    shift = get_shift(shift_id)

    if shift is None:
        return jsonify({"error": "Smena neexistuje."}), 404

    new_date = payload.get("shift_date", shift[2])
    new_start = payload.get("start_time", shift[3])
    new_end = payload.get("end_time", shift[4])

    errors = validate_shift(
        shift[1],
        new_date,
        new_start,
        new_end,
        department_id=shift[6],
        exclude_shift_id=shift_id,
    )

    if errors:
        return jsonify({"error": " ".join(errors)}), 400

    move_shift(shift_id, new_date, new_start, new_end)

    return jsonify({"ok": True})


if __name__ == "__main__":
    create_tables()
    app.run(debug=True)
