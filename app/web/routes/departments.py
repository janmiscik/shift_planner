"""Oddelenia."""

from flask import (
    flash,
    redirect,
    render_template,
    request,
)
from app.services.department_service import (
    add_department,
    get_department,
    get_departments,
    set_department_active,
    update_department,
)
from app.web.forms import (
    DepartmentForm,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _branch_form_choices,
    _resolve_branch_id,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Oddelenia
# ---------------------------------------------------------------------

@bp.route("/departments")
def departments_page():
    departments = get_departments(_scope())

    return render_template(
        "departments.html",
        departments=departments,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/departments/add", methods=["GET", "POST"])
def add_department_page():
    form = DepartmentForm()
    branches, form.branch_id.choices = _branch_form_choices()

    if form.validate_on_submit():
        branch_id = _resolve_branch_id(form.branch_id.data)

        if branch_id is None:
            flash("Nie je dostupná žiadna aktívna pobočka.", "error")

            return render_template(
                "add_department.html", form=form, branches=branches
            )

        department_id = add_department(
            form.name.data,
            form.min_staff.data,
            branch_id=branch_id,
        )

        if department_id is None:
            flash(
                "Oddelenie s týmto názvom už existuje v tejto pobočke.",
                "error",
            )

            return render_template(
                "add_department.html", form=form, branches=branches
            )

        return redirect("/departments")

    return render_template(
        "add_department.html", form=form, branches=branches
    )


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
            flash(
                "Oddelenie s týmto názvom už existuje v tejto pobočke.",
                "error",
            )

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
