"""Webová aplikácia Shift Planner.

Táto vrstva sa stará výlučne o spracovanie HTTP požiadaviek
(parsovanie vstupov, presmerovania, JSON odpovede). Biznis logika
a výpočty (kolízie smien, týždenné fondy, kalendárna mriežka,
exporty) žijú v ``app/services``.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, Flask, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf import CSRFProtect
from wtforms.validators import DataRequired, Length

from app.data.database import DEFAULT_DATABASE_PATH
from app.extensions import db, login_manager, migrate

load_dotenv()

from app.services.auth_service import (
    create_employee_user,
    delete_user,
    get_user_by_employee_id,
    get_user_by_id,
    get_user_by_username,
    set_password,
    set_username,
    username_exists,
    verify_password,
)

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

from app.services.absence_service import (
    add_absence,
    delete_absence,
    get_absence,
    get_absences,
    get_absences_by_employee,
    update_absence,
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

from app.services.capacity_service import get_understaffed_shifts

from app.web.forms import (
    AbsenceForm,
    CredentialsForm,
    DepartmentForm,
    EmployeeDepartmentForm,
    EmployeeForm,
    LoginForm,
    ShiftForm,
)


bp = Blueprint("main", __name__)

csrf = CSRFProtect()

_DEFAULT_SECRET_KEY = "dev-docasny-kluc-zmen-v-produkcii"

# Routy dostupné bez prihlásenia (len prihlasovacia stránka a
# statické súbory).
_PUBLIC_ENDPOINTS = {"main.login_page", "static"}

# Routy, na ktoré má prístup aj rola "employee" (len na čítanie
# svojho vlastného rozpisu). Všetko ostatné je predvolene zakázané
# pre túto rolu - nová routa sa musí explicitne pridať sem, aby k nej
# mal zamestnanec prístup (bezpečnejšie ako opačný zoznam zákazov).
_EMPLOYEE_ALLOWED_ENDPOINTS = {
    "main.my_schedule",
    "main.my_schedule_export_ics",
    "main.logout_page",
}


@login_manager.user_loader
def _load_user(user_id):
    return get_user_by_id(user_id)


@bp.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None

    if not current_user.is_authenticated:
        return redirect(url_for("main.login_page", next=request.path))

    if (
        current_user.role == "employee"
        and request.endpoint not in _EMPLOYEE_ALLOWED_ENDPOINTS
    ):
        return render_template("forbidden.html"), 403

    return None


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


@bp.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        if current_user.role == "employee":
            return redirect("/my-schedule")

        return redirect("/")

    form = LoginForm()

    if form.validate_on_submit():
        user = get_user_by_username(form.username.data)

        if user is None or not verify_password(user, form.password.data):
            flash("Nesprávne používateľské meno alebo heslo.", "error")
        else:
            login_user(user)

            if user.role == "employee":
                return redirect("/my-schedule")

            next_url = request.args.get("next")

            return redirect(next_url or "/")

    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout_page():
    logout_user()

    return redirect("/login")


@bp.route("/my-schedule")
def my_schedule():
    if current_user.role != "employee" or current_user.employee_id is None:
        return redirect("/")

    employee = get_employee(current_user.employee_id)
    shifts = get_shifts_by_employee(current_user.employee_id)
    absences = get_absences_by_employee(current_user.employee_id)

    return render_template(
        "my_schedule.html",
        employee=employee,
        shifts=shifts,
        absences=absences,
    )


@bp.route("/my-schedule/export.ics")
def my_schedule_export_ics():
    if current_user.role != "employee" or current_user.employee_id is None:
        return redirect("/")

    employee = get_employee(current_user.employee_id)
    shifts = get_shifts_by_employee(current_user.employee_id)
    calendar_name = f"Moje smeny - {employee[1]} {employee[2]}"

    ics = build_shifts_ics(shifts, calendar_name=calendar_name)

    return send_file(
        ics,
        as_attachment=True,
        download_name=f"moje_smeny_{employee[1]}_{employee[2]}.ics",
        mimetype="text/calendar",
    )


@bp.route("/")
def home():
    employees = get_employees()
    shifts = get_shifts()

    return render_template(
        "dashboard.html",
        employees=employees,
        shifts=shifts,
    )


@bp.route("/admin/backup")
def admin_backup():
    """Stiahne aktuálnu databázu ako súbor (a zároveň ju zazálohuje
    aj do priečinka ``backups/`` na serveri)."""

    from flask import current_app

    from app.services.backup_service import create_backup

    database_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    database_path = database_uri.replace("sqlite:///", "", 1)

    backup_path = create_backup(database_path, label="stiahnutie")

    if backup_path is None:
        return "Databáza ešte neexistuje, niet čo zálohovať.", 404

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name,
        mimetype="application/x-sqlite3",
    )


# ---------------------------------------------------------------------
# Zamestnanci
# ---------------------------------------------------------------------

@bp.route("/employees")
def employees_page():
    employees = get_employees()

    return render_template("employees.html", employees=employees)


@bp.route("/employees/add", methods=["GET", "POST"])
def add_employee_page():
    form = EmployeeForm()
    credentials_form = CredentialsForm(meta={"csrf": False})
    credentials_form.password.validators = [
        DataRequired(message="Zadaj heslo."),
        Length(min=6, message="Heslo musí mať aspoň 6 znakov."),
    ]

    if form.validate_on_submit():
        credentials_valid = credentials_form.validate()

        if credentials_valid and username_exists(credentials_form.username.data):
            flash("Toto používateľské meno už niekto používa.", "error")
        elif credentials_valid:
            employee_id = add_employee(
                form.first_name.data,
                form.last_name.data,
                form.position.data,
                form.weekly_hours.data,
            )

            create_employee_user(
                employee_id,
                credentials_form.username.data,
                credentials_form.password.data,
            )

            return redirect("/employees")

    return render_template(
        "add_employee.html",
        form=form,
        credentials_form=credentials_form,
    )


@bp.route("/employees/edit/<int:employee_id>", methods=["GET", "POST"])
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
    employee_user = get_user_by_employee_id(employee_id)
    credentials_form = CredentialsForm()

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
                employee_user=employee_user,
                credentials_form=credentials_form,
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
        employee_user=employee_user,
        credentials_form=credentials_form,
        form=form,
    )


@bp.route(
    "/employees/<int:employee_id>/credentials/create",
    methods=["POST"],
)
def create_employee_credentials_page(employee_id):
    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    form = CredentialsForm()
    form.password.validators = [
        DataRequired(message="Zadaj heslo."),
        Length(min=6, message="Heslo musí mať aspoň 6 znakov."),
    ]

    if form.validate_on_submit():
        if username_exists(form.username.data):
            flash("Toto používateľské meno už niekto používa.", "error")
        else:
            create_employee_user(
                employee_id,
                form.username.data,
                form.password.data,
            )

            return redirect(f"/employees/edit/{employee_id}")

    assignments = get_employee_departments(employee_id)
    total_weekly_hours = get_employee_department_hours(employee_id)
    employee_form = EmployeeForm()
    employee_form.first_name.data = employee[1]
    employee_form.last_name.data = employee[2]
    employee_form.position.data = employee[3]
    employee_form.weekly_hours.data = employee[4]

    return render_template(
        "edit_employee.html",
        employee=employee,
        assignments=assignments,
        total_weekly_hours=total_weekly_hours,
        employee_user=None,
        credentials_form=form,
        form=employee_form,
    )


@bp.route(
    "/employees/<int:employee_id>/credentials/update",
    methods=["POST"],
)
def update_employee_credentials_page(employee_id):
    employee_user = get_user_by_employee_id(employee_id)

    if employee_user is None:
        return "Zamestnanec nemá prihlasovací účet.", 404

    form = CredentialsForm()

    if form.validate_on_submit():
        if not username_exists(
            form.username.data, exclude_user_id=employee_user.id
        ):
            set_username(employee_user.id, form.username.data)
        else:
            flash("Toto používateľské meno už niekto používa.", "error")
            return redirect(f"/employees/edit/{employee_id}")

        if form.password.data:
            set_password(employee_user.id, form.password.data)

        flash("Prihlasovacie údaje boli aktualizované.", "error")

    return redirect(f"/employees/edit/{employee_id}")


@bp.route(
    "/employees/<int:employee_id>/credentials/delete",
    methods=["POST"],
)
def delete_employee_credentials_page(employee_id):
    employee_user = get_user_by_employee_id(employee_id)

    if employee_user is not None:
        delete_user(employee_user.id)

    return redirect(f"/employees/edit/{employee_id}")


@bp.route(
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


@bp.route(
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


@bp.route(
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


@bp.route("/employees/<int:employee_id>/export.ics")
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


@bp.route("/employees/toggle/<int:employee_id>", methods=["POST"])
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

@bp.route("/departments")
def departments_page():
    departments = get_departments()

    return render_template("departments.html", departments=departments)


@bp.route("/departments/add", methods=["GET", "POST"])
def add_department_page():
    form = DepartmentForm()

    if form.validate_on_submit():
        department_id = add_department(form.name.data, form.min_staff.data)

        if department_id is None:
            flash("Oddelenie s týmto názvom už existuje.", "error")
            return render_template("add_department.html", form=form)

        return redirect("/departments")

    return render_template("add_department.html", form=form)


@bp.route("/departments/edit/<int:department_id>", methods=["GET", "POST"])
def edit_department_page(department_id):
    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    form = DepartmentForm()

    if request.method == "GET":
        form.name.data = department[1]
        form.min_staff.data = department[3]

    if form.validate_on_submit():
        updated = update_department(
            department_id, form.name.data, form.min_staff.data
        )

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


@bp.route("/departments/toggle/<int:department_id>", methods=["POST"])
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

@bp.route("/shifts")
def shifts_page():
    shifts = get_shifts()

    return render_template("shifts.html", shifts=shifts)


@bp.route("/shifts/export.xlsx")
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


@bp.route("/shifts/export.pdf")
def export_shifts_pdf():
    shifts = get_shifts()
    pdf = build_shifts_pdf(shifts, title="Harmonogram smien - všetky")

    return send_file(
        pdf,
        as_attachment=True,
        download_name="smeny.pdf",
        mimetype="application/pdf",
    )


@bp.route("/shifts/export.ics")
def export_shifts_ics():
    shifts = get_shifts()
    ics = build_shifts_ics(shifts, calendar_name="Shift Planner - všetky smeny")

    return send_file(
        ics,
        as_attachment=True,
        download_name="smeny.ics",
        mimetype="text/calendar",
    )


@bp.route("/shifts/add", methods=["GET", "POST"])
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


@bp.route("/shifts/edit/<int:shift_id>", methods=["GET", "POST"])
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


@bp.route("/shifts/delete/<int:shift_id>", methods=["POST"])
def delete_shift_page(shift_id):
    delete_shift(shift_id)

    return redirect("/shifts")


# ---------------------------------------------------------------------
# Absencie (dovolenka, PN, OČR, náhradné voľno)
# ---------------------------------------------------------------------

@bp.route("/absences")
def absences_page():
    employee_id = request.args.get("employee_id", type=int)

    if employee_id:
        absences = get_absences_by_employee(employee_id)
    else:
        absences = get_absences()

    employees = get_employees()

    return render_template(
        "absences.html",
        absences=absences,
        employees=employees,
        selected_employee_id=employee_id,
    )


@bp.route("/absences/add", methods=["GET", "POST"])
def add_absence_page():
    employees = get_employees()

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices()

    if request.method == "GET":
        preselected_employee_id = request.args.get("employee_id", type=int)

        if preselected_employee_id:
            form.employee_id.data = preselected_employee_id

        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    if form.end_date.data < form.start_date.data:
        flash("Dátum konca nemôže byť skorší ako dátum začiatku.", "error")

        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    employee_id = form.employee_id.data
    start_date = form.start_date.data.isoformat()
    end_date = form.end_date.data.isoformat()

    add_absence(
        employee_id,
        form.absence_type.data,
        start_date,
        end_date,
        note=form.note.data,
    )

    conflicting_shifts = [
        shift
        for shift in get_shifts_by_employee(employee_id)
        if start_date <= shift[3] <= end_date
    ]

    if conflicting_shifts:
        flash(
            f"Pozor: zamestnanec má v tomto období už naplánovaných "
            f"{len(conflicting_shifts)} smien - skontroluj ich na "
            "stránke /shifts.",
            "error",
        )

    return redirect("/absences")


@bp.route("/absences/edit/<int:absence_id>", methods=["GET", "POST"])
def edit_absence_page(absence_id):
    absence = get_absence(absence_id)

    if absence is None:
        return "Neprítomnosť neexistuje.", 404

    employees = get_employees()

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices()

    if request.method == "GET":
        form.employee_id.data = absence[1]
        form.absence_type.data = absence[2]
        form.start_date.data = date.fromisoformat(absence[3])
        form.end_date.data = date.fromisoformat(absence[4])
        form.note.data = absence[5]

        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    if form.end_date.data < form.start_date.data:
        flash("Dátum konca nemôže byť skorší ako dátum začiatku.", "error")

        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    update_absence(
        absence_id,
        form.absence_type.data,
        form.start_date.data.isoformat(),
        form.end_date.data.isoformat(),
        note=form.note.data,
    )

    return redirect("/absences")


@bp.route("/absences/delete/<int:absence_id>", methods=["POST"])
def delete_absence_page(absence_id):
    delete_absence(absence_id)

    return redirect("/absences")


# ---------------------------------------------------------------------
# Kalendár
# ---------------------------------------------------------------------

@bp.route("/calendar")
def calendar_page():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    employee_id = request.args.get("employee_id", type=int)
    department_id = request.args.get("department_id", type=int)

    employees = get_employees()
    departments = get_departments()

    calendar_days = build_calendar_days(year, month)

    first_day, last_day = month_bounds(year, month)
    understaffed_shifts = get_understaffed_shifts(first_day, last_day)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        calendar_days=calendar_days,
        employees=employees,
        departments=departments,
        selected_employee_id=employee_id,
        selected_department_id=department_id,
        understaffed_shifts=understaffed_shifts,
    )


@bp.route("/calendar/export.xlsx")
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


@bp.route("/calendar/export.pdf")
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


@bp.route("/calendar/export.ics")
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

@bp.route("/api/employees/<int:employee_id>/departments")
def api_employee_departments(employee_id):
    assignments = get_employee_departments(employee_id)

    return jsonify(
        [
            {"id": assignment[1], "name": assignment[2]}
            for assignment in assignments
        ]
    )


@bp.route("/api/shifts")
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


@bp.route("/api/shifts/<int:shift_id>/move", methods=["POST"])
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


def create_app(database_path=None):
    """Vytvorí a nakonfiguruje inštanciu Flask appky.

    ``database_path`` - explicitná cesta k SQLite súboru. Ak sa
    nezadá, použije sa predvolená cesta z ``app/data/database.py``.
    Testy si sem posielajú vlastnú dočasnú cestu, aby mal každý test
    úplne izolovanú databázu (nutné pre správne fungovanie
    SQLAlchemy - engine sa viaže na appku pri jej vytvorení, nie pri
    každom volaní ako predtým s ručnými sqlite3 pripojeniami).
    """

    app = Flask(__name__)

    app.secret_key = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)

    if app.secret_key == _DEFAULT_SECRET_KEY:
        print(
            "UPOZORNENIE: beží sa s predvoleným (nebezpečným) SECRET_KEY. "
            "Pred nasadením do produkcie nastav premennú prostredia "
            "SECRET_KEY na náhodný, tajný reťazec - napr.:\n"
            "  python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    resolved_path = database_path or DEFAULT_DATABASE_PATH

    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{resolved_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"

    db.init_app(app)
    migrate.init_app(app, db, directory=str(migrations_dir))
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "main.login_page"
    login_manager.login_message = "Prosím, prihlás sa."
    login_manager.login_message_category = "error"

    app.register_blueprint(bp)

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
