"""Webová aplikácia Shift Planner."""

from flask import Flask, render_template, request, redirect

from app.data.database import create_tables
from app.services.employee_service import add_employee, get_employees
from app.services.shift_service import add_shift, get_shifts


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

    return render_template(
        "add_shift.html",
        employees=employees,
    )


if __name__ == "__main__":
    app.run(debug=True)
