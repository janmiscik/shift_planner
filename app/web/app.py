"""Webová aplikácia Shift Planner."""

from calendar import monthrange
from datetime import date
from flask import Flask, render_template, request, redirect

from app.data.database import create_tables

from app.services.employee_service import (
    add_employee,
    get_employee,
    get_employees,
    update_employee,
    set_employee_active,
)

from app.services.shift_service import (
    add_shift,
    get_shift,
    get_shifts,
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


@app.route("/employees/add", methods=["GET", "POST"])
def add_employee_page():
    if request.method == "POST":
        add_employee(
            first_name=request.form["first_name"],
            last_name=request.form["last_name"],
            position=request.form["position"],
            employment_type=request.form["employment_type"],
        )

        return redirect("/")

    return render_template("add_employee.html")


@app.route("/employees/edit/<int:employee_id>", methods=["GET", "POST"])
def edit_employee_page(employee_id):
    create_tables()

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    if request.method == "POST":
        update_employee(
            employee_id=employee_id,
            first_name=request.form["first_name"],
            last_name=request.form["last_name"],
            position=request.form["position"],
            employment_type=request.form["employment_type"],
        )

        return redirect("/employees")

    return render_template(
        "edit_employee.html",
        employee=employee,
    )


@app.route("/employees")
def employees_page():
    create_tables()

    employees = get_employees()

    return render_template(
        "employees.html",
        employees=employees,
    )


@app.route("/employees/toggle/<int:employee_id>", methods=["POST"])
def toggle_employee_page(employee_id):
    create_tables()

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    new_status = 0 if employee[5] else 1

    set_employee_active(
        employee_id=employee_id,
        active=new_status,
    )

    return redirect("/employees")


@app.route("/shifts")
def shifts_page():
    create_tables()

    shifts = get_shifts()

    return render_template(
        "shifts.html",
        shifts=shifts,
    )


@app.route("/shifts/add", methods=["GET", "POST"])
def add_shift_page():
    create_tables()

    employees = get_employees()

    if request.method == "POST":
        add_shift(
            employee_id=request.form["employee_id"],
            shift_date=request.form["shift_date"],
            start_time=request.form["start_time"],
            end_time=request.form["end_time"],
            shift_type=request.form["shift_type"],
        )

        return redirect("/shifts")

    selected_date = request.args.get("date", "")

    return render_template(
        "add_shift.html",
        employees=employees,
        selected_date=selected_date,
    )


@app.route("/shifts/edit/<int:shift_id>", methods=["GET", "POST"])
def edit_shift_page(shift_id):
    create_tables()

    shift = get_shift(shift_id)
    employees = get_employees()

    if shift is None:
        return "Smena neexistuje.", 404

    if request.method == "POST":
        update_shift(
            shift_id=shift_id,
            employee_id=request.form["employee_id"],
            shift_date=request.form["shift_date"],
            start_time=request.form["start_time"],
            end_time=request.form["end_time"],
            shift_type=request.form["shift_type"],
        )

        return redirect("/shifts")

    return render_template(
        "edit_shift.html",
        shift=shift,
        employees=employees,
    )


@app.route("/shifts/delete/<int:shift_id>", methods=["POST"])
def delete_shift_page(shift_id):
    create_tables()

    delete_shift(shift_id)

    return redirect("/shifts")


@app.route("/calendar")
def calendar_page():
    create_tables()

    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    if month < 1:
        month = 12
        year -= 1

    if month > 12:
        month = 1
        year += 1

    shifts = get_shifts()

    calendar_days = []

    first_weekday, days_in_month = monthrange(year, month)

    for _ in range(first_weekday):
        calendar_days.append(None)

    for day in range(1, days_in_month + 1):
        calendar_days.append(day)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        calendar_days=calendar_days,
        shifts=shifts,
    )


if __name__ == "__main__":
    app.run(debug=True)
