"""Webová aplikácia Shift Planner."""

from calendar import monthrange
from datetime import date

from flask import Flask, redirect, render_template, request

from app.data.database import create_tables

from app.services.employee_service import (
    add_employee,
    get_employee,
    get_employees,
    update_employee,
    set_employee_active,
)

from app.services.department_service import (
    add_department,
    get_department,
    get_departments,
    update_department,
    set_department_active,
)

from app.services.employee_department_service import (
    add_employee_department,
    get_employee_departments,
    get_employee_department_hours,
)

from app.services.shift_service import (
    add_shift,
    get_shift,
    get_shifts,
    get_shifts_by_employee,
    update_shift,
    delete_shift,
)


app = Flask(__name__)


@app.route("/")
def home():
    create_tables()

    employees = get_employees()
    shifts = get_shifts()

    return render_template(
        "dashboard.html",
        employees=employees,
        shifts=shifts,
    )


@app.route("/employees")
def employees_page():
    create_tables()

    employees = get_employees()

    return render_template(
        "employees.html",
        employees=employees,
    )


@app.route("/employees/add", methods=["GET", "POST"])
def add_employee_page():
    create_tables()

    if request.method == "POST":
        add_employee(
            request.form["first_name"],
            request.form["last_name"],
            request.form["position"],
            request.form["employment_type"],
        )

        return redirect("/employees")

    return render_template("add_employee.html")


@app.route(
    "/employees/edit/<int:employee_id>",
    methods=["GET", "POST"],
)
def edit_employee_page(employee_id):
    create_tables()

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    if request.method == "POST":
        update_employee(
            employee_id,
            request.form["first_name"],
            request.form["last_name"],
            request.form["position"],
            request.form["employment_type"],
        )

        return redirect("/employees")

    assignments = get_employee_departments(employee_id)

    total_weekly_hours = get_employee_department_hours(
        employee_id
    )

    return render_template(
        "edit_employee.html",
        employee=employee,
        assignments=assignments,
        total_weekly_hours=total_weekly_hours,
    )


@app.route(
    "/employees/<int:employee_id>/departments/add",
    methods=["GET", "POST"],
)
def add_employee_department_page(employee_id):
    create_tables()

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    departments = get_departments()

    if request.method == "POST":
        add_employee_department(
            employee_id=employee_id,
            department_id=request.form["department_id"],
            weekly_hours=request.form["weekly_hours"],
        )

        return redirect(
            f"/employees/edit/{employee_id}"
        )

    return render_template(
        "add_employee_department.html",
        employee=employee,
        departments=departments,
    )


@app.route(
    "/employees/toggle/<int:employee_id>",
    methods=["POST"],
)
def toggle_employee_page(employee_id):
    create_tables()

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    new_status = 0 if employee[5] else 1

    set_employee_active(
        employee_id,
        new_status,
    )

    return redirect("/employees")


@app.route("/departments")
def departments_page():
    create_tables()

    departments = get_departments()

    return render_template(
        "departments.html",
        departments=departments,
    )


@app.route(
    "/departments/add",
    methods=["GET", "POST"],
)
def add_department_page():
    create_tables()

    if request.method == "POST":
        add_department(
            request.form["name"]
        )

        return redirect("/departments")

    return render_template("add_department.html")


@app.route(
    "/departments/edit/<int:department_id>",
    methods=["GET", "POST"],
)
def edit_department_page(department_id):
    create_tables()

    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    if request.method == "POST":
        update_department(
            department_id,
            request.form["name"],
        )

        return redirect("/departments")

    return render_template(
        "edit_department.html",
        department=department,
    )


@app.route(
    "/departments/toggle/<int:department_id>",
    methods=["POST"],
)
def toggle_department_page(department_id):
    create_tables()

    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    new_status = 0 if department[2] else 1

    set_department_active(
        department_id,
        new_status,
    )

    return redirect("/departments")


@app.route("/shifts")
def shifts_page():
    create_tables()

    shifts = get_shifts()

    return render_template(
        "shifts.html",
        shifts=shifts,
    )


@app.route(
    "/shifts/add",
    methods=["GET", "POST"],
)
def add_shift_page():
    create_tables()

    employees = get_employees()

    if request.method == "POST":
        add_shift(
            request.form["employee_id"],
            request.form["shift_date"],
            request.form["start_time"],
            request.form["end_time"],
            request.form["shift_type"],
        )

        return redirect("/shifts")

    selected_date = request.args.get(
        "date",
        "",
    )

    return render_template(
        "add_shift.html",
        employees=employees,
        selected_date=selected_date,
    )


@app.route(
    "/shifts/edit/<int:shift_id>",
    methods=["GET", "POST"],
)
def edit_shift_page(shift_id):
    create_tables()

    shift = get_shift(shift_id)
    employees = get_employees()

    if shift is None:
        return "Smena neexistuje.", 404

    if request.method == "POST":
        update_shift(
            shift_id,
            request.form["employee_id"],
            request.form["shift_date"],
            request.form["start_time"],
            request.form["end_time"],
            request.form["shift_type"],
        )

        return redirect("/shifts")

    return render_template(
        "edit_shift.html",
        shift=shift,
        employees=employees,
    )


@app.route(
    "/shifts/delete/<int:shift_id>",
    methods=["POST"],
)
def delete_shift_page(shift_id):
    create_tables()

    delete_shift(shift_id)

    return redirect("/shifts")


@app.route("/calendar")
def calendar_page():
    create_tables()

    today = date.today()

    year = request.args.get(
        "year",
        today.year,
        type=int,
    )

    month = request.args.get(
        "month",
        today.month,
        type=int,
    )

    employee_id = request.args.get(
        "employee_id",
        type=int,
    )

    employees = get_employees()

    if employee_id:
        shifts = get_shifts_by_employee(employee_id)
    else:
        shifts = get_shifts()

    calendar_days = []

    first_weekday, days_in_month = monthrange(
        year,
        month,
    )

    for _ in range(first_weekday):
        calendar_days.append(None)

    for day in range(
        1,
        days_in_month + 1,
    ):
        calendar_days.append(day)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        calendar_days=calendar_days,
        shifts=shifts,
        employees=employees,
        selected_employee_id=employee_id,
    )


if __name__ == "__main__":
    app.run(debug=True)