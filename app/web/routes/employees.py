"""Zamestnanci, ich účty, funkcia manažéra a priradenie k oddeleniam."""

from flask import (
    flash,
    redirect,
    render_template,
    request,
    send_file,
)
from wtforms.validators import DataRequired, Length
from app.services.branch_service import (
    get_branches,
)
from app.services.auth_service import (
    create_employee_user,
    delete_user,
    get_user_by_employee_id,
    set_manager_function,
    set_password,
    set_username,
    username_exists,
)
from app.services.employee_service import (
    add_employee,
    get_employee,
    get_employees,
    set_employee_active,
    update_employee,
)
from app.services.department_service import (
    get_department,
    get_departments,
)
from app.services.employee_department_service import (
    add_employee_department,
    can_add_employee_department,
    can_update_employee_department,
    delete_employee_department,
    get_employee_department,
    get_employee_department_hours,
    get_employee_departments,
    update_employee_department,
)
from app.services.shift_service import (
    get_shifts_by_employee,
)
from app.services.export_service import (
    build_shifts_ics,
)
from app.web.forms import (
    CredentialsForm,
    EmployeeDepartmentForm,
    EmployeeForm,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _active_department_choices,
    _branch_form_choices,
    _branch_in_access,
    _resolve_branch_id,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Zamestnanci
# ---------------------------------------------------------------------

@bp.route("/employees")
def employees_page():
    employees = get_employees(_scope())

    return render_template(
        "employees.html",
        employees=employees,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/employees/add", methods=["GET", "POST"])
def add_employee_page():
    form = EmployeeForm()
    branches, form.branch_id.choices = _branch_form_choices()
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
            branch_id = _resolve_branch_id(form.branch_id.data)

            if branch_id is None:
                flash("Nie je dostupná žiadna aktívna pobočka.", "error")

                return render_template(
                    "add_employee.html",
                    form=form,
                    credentials_form=credentials_form,
                    branches=branches,
                )

            employee_id = add_employee(
                form.first_name.data,
                form.last_name.data,
                form.position.data,
                form.weekly_hours.data,
                branch_id=branch_id,
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
        branches=branches,
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
                branches=get_branches(),
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
        branches=get_branches(),
    )


@bp.route(
    "/employees/<int:employee_id>/manager",
    methods=["POST"],
)
def update_employee_manager_page(employee_id):
    """Pridelí/odoberie zamestnancovi funkciu manažéra (jeden účet) a
    určí pobočky, v ktorých smie plánovať smeny. Len admin."""

    employee = get_employee(employee_id)

    if employee is None:
        return "Zamestnanec neexistuje.", 404

    if get_user_by_employee_id(employee_id) is None:
        flash(
            "Najprv vytvor zamestnancovi prihlasovací účet.", "error"
        )

        return redirect(f"/employees/edit/{employee_id}")

    enabled = request.form.get("manager_enabled") == "1"

    valid_branch_ids = {branch[0] for branch in get_branches()}
    branch_ids = []

    for raw in request.form.getlist("branch_ids"):
        if raw.isdigit() and int(raw) in valid_branch_ids:
            branch_ids.append(int(raw))

    if enabled and not branch_ids:
        flash(
            "Manažér musí mať priradenú aspoň jednu pobočku.", "error"
        )

        return redirect(f"/employees/edit/{employee_id}")

    set_manager_function(employee_id, enabled, branch_ids)

    flash(
        "Zamestnanec je teraz manažér." if enabled
        else "Funkcia manažéra bola zamestnancovi odobratá.",
        "success",
    )

    return redirect(f"/employees/edit/{employee_id}")


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

        flash("Prihlasovacie údaje boli aktualizované.", "success")

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

    departments = get_departments([employee[6]])

    form = EmployeeDepartmentForm()
    form.department_id.choices = _active_department_choices([employee[6]])

    if form.validate_on_submit():
        department = get_department(form.department_id.data)

        if (
            department is None
            or not department[2]
            or department[4] != employee[6]
        ):
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

    departments = get_departments([employee[6]])

    form = EmployeeDepartmentForm()
    form.department_id.choices = _active_department_choices([employee[6]])

    if request.method == "GET":
        form.department_id.data = assignment[2]
        form.weekly_hours.data = assignment[3]

    if form.validate_on_submit():
        department = get_department(form.department_id.data)

        if (
            department is None
            or not department[2]
            or department[4] != employee[6]
        ):
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

    if employee is None or not _branch_in_access(employee[6]):
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
