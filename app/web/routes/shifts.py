"""Smeny: zoznam, exporty, pridanie, úprava a zmazanie."""

from datetime import date
from flask import (
    flash,
    redirect,
    render_template,
    request,
    send_file,
)
from flask_login import (
    current_user,
)
from app.services.publish_service import (
    get_draft_shift_ids,
)
from app.services.access_service import (
    accessible_branch_ids,
    shift_branch_id,
)
from app.services.employee_service import (
    get_employee,
    get_employees,
)
from app.services.shift_service import (
    add_shift,
    delete_shift,
    get_shift,
    get_shifts,
    update_shift,
    validate_shift,
)
from app.services.export_service import (
    build_shifts_ics,
    build_shifts_pdf,
    build_shifts_workbook,
)
from app.web.forms import (
    ShiftForm,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _active_department_choices,
    _active_employee_choices,
    _branch_in_access,
    _employee_in_access,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Smeny
# ---------------------------------------------------------------------

@bp.route("/shifts")
def shifts_page():
    scope = _scope()

    shifts = get_shifts(scope)

    return render_template(
        "shifts.html",
        shifts=shifts,
        draft_ids=get_draft_shift_ids(branch_ids=scope),
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/shifts/export.xlsx")
def export_shifts_xlsx():
    shifts = get_shifts(_scope())
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
    shifts = get_shifts(_scope())
    pdf = build_shifts_pdf(shifts, title="Harmonogram smien - všetky")

    return send_file(
        pdf,
        as_attachment=True,
        download_name="smeny.pdf",
        mimetype="application/pdf",
    )


@bp.route("/shifts/export.ics")
def export_shifts_ics():
    shifts = get_shifts(_scope())
    ics = build_shifts_ics(shifts, calendar_name="Shift Planner - všetky smeny")

    return send_file(
        ics,
        as_attachment=True,
        download_name="smeny.ics",
        mimetype="text/calendar",
    )


@bp.route("/shifts/add", methods=["GET", "POST"])
def add_shift_page():
    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices(access)
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name)
        for dep_id, name in _active_department_choices(access)
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

    if shift is None or not _employee_in_access(shift[1]):
        return "Smena neexistuje.", 404

    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices(access)
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name)
        for dep_id, name in _active_department_choices(access)
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
    if not _branch_in_access(shift_branch_id(shift_id)):
        return "Smena neexistuje.", 404

    delete_shift(shift_id)

    return redirect("/shifts")
