"""Webová aplikácia Shift Planner."""

from flask import Flask, render_template, request, redirect

from app.data.database import create_tables
from app.services.employee_service import add_employee, get_employees


app = Flask(__name__)


@app.route("/")
def home():
    create_tables()

    employees = get_employees()

    return render_template(
        "employees.html",
        employees=employees,
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


if __name__ == "__main__":
    app.run(debug=True)