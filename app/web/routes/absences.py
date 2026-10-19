"""Absencie (dovolenka, PN, OČR, náhradné voľno)."""

from datetime import date
from flask import (
    flash,
    redirect,
    render_template,
    request,
)
from flask_login import (
    current_user,
)
from app.services.access_service import (
    accessible_branch_ids,
    absence_branch_id,
)
from app.services.employee_service import (
    get_employees,
)
from app.services.shift_service import (
    get_shifts_by_employee,
)
from app.services.absence_service import (
    add_absence,
    delete_absence,
    get_absence,
    get_absences,
    get_absences_by_employee,
    update_absence,
)
from app.web.forms import (
    AbsenceForm,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _active_employee_choices,
    _branch_in_access,
    _employee_in_access,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Absencie (dovolenka, PN, OČR, náhradné voľno)
# ---------------------------------------------------------------------

@bp.route("/absences")
def absences_page():
    employee_id = request.args.get("employee_id", type=int)
    scope = _scope()

    if employee_id:
        if _employee_in_access(employee_id):
            absences = get_absences_by_employee(employee_id)
        else:
            absences = []
    else:
        absences = get_absences(scope)

    employees = get_employees(scope)

    return render_template(
        "absences.html",
        absences=absences,
        employees=employees,
        selected_employee_id=employee_id,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/absences/add", methods=["GET", "POST"])
def add_absence_page():
    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices(access)

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

    if absence is None or not _employee_in_access(absence[1]):
        return "Neprítomnosť neexistuje.", 404

    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices(access)

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
    if not _branch_in_access(absence_branch_id(absence_id)):
        return "Neprítomnosť neexistuje.", 404

    delete_absence(absence_id)

    return redirect("/absences")
