"""Správa pobočiek a účtov adminov a manažérov (len admin)."""

from flask import (
    flash,
    redirect,
    render_template,
    request,
)
from flask_login import (
    current_user,
)
from wtforms.validators import DataRequired, Length, Optional
from app.orm_models import ROLE_ADMIN, ROLE_MANAGER
from app.services.branch_service import (
    add_branch,
    get_branch,
    get_branches,
    get_branches_with_counts,
    set_branch_active,
    update_branch,
)
from app.services.auth_service import (
    count_admins,
    create_admin,
    create_manager,
    delete_user,
    get_staff_users,
    get_user_by_id,
    set_password,
    set_user_branches,
    set_username,
    username_exists,
)
from app.web.forms import (
    BranchForm,
    StaffUserForm,
)

from app.web.core import bp


# ---------------------------------------------------------------------
# Pobočky (len admin)
# ---------------------------------------------------------------------

@bp.route("/branches")
def branches_page():
    return render_template(
        "branches.html", branches=get_branches_with_counts()
    )


@bp.route("/branches/add", methods=["GET", "POST"])
def add_branch_page():
    form = BranchForm()

    if form.validate_on_submit():
        branch_id = add_branch(form.name.data.strip())

        if branch_id is None:
            flash("Pobočka s týmto názvom už existuje.", "error")
        else:
            flash("Pobočka bola pridaná.", "success")

            return redirect("/branches")

    return render_template("branch_form.html", form=form, branch=None)


@bp.route("/branches/edit/<int:branch_id>", methods=["GET", "POST"])
def edit_branch_page(branch_id):
    branch = get_branch(branch_id)

    if branch is None:
        return "Pobočka neexistuje.", 404

    form = BranchForm()

    if request.method == "GET":
        form.name.data = branch[1]

    if form.validate_on_submit():
        if update_branch(branch_id, form.name.data.strip()):
            flash("Pobočka bola upravená.", "success")

            return redirect("/branches")

        flash("Pobočka s týmto názvom už existuje.", "error")

    return render_template("branch_form.html", form=form, branch=branch)


@bp.route("/branches/toggle/<int:branch_id>", methods=["POST"])
def toggle_branch_page(branch_id):
    branch = get_branch(branch_id)

    if branch is None:
        return "Pobočka neexistuje.", 404

    if branch[2]:
        active_branches = get_branches(only_active=True)

        if len(active_branches) <= 1:
            flash(
                "Nedá sa deaktivovať poslednú aktívnu pobočku.",
                "error",
            )

            return redirect("/branches")

    set_branch_active(branch_id, 0 if branch[2] else 1)

    return redirect("/branches")


# ---------------------------------------------------------------------
# Účty adminov a manažérov (len admin)
# ---------------------------------------------------------------------

def _staff_user_or_none(user_id):
    user = get_user_by_id(user_id)

    if user is None or user.role not in (ROLE_ADMIN, ROLE_MANAGER):
        return None

    return user


@bp.route("/users")
def users_page():
    return render_template("users.html", users=get_staff_users())


@bp.route("/users/add", methods=["GET", "POST"])
def add_user_page():
    form = StaffUserForm()
    branches = get_branches()
    form.branch_ids.choices = [(branch[0], branch[1]) for branch in branches]
    form.password.validators = [
        DataRequired(message="Zadaj heslo."),
        Length(min=6, message="Heslo musí mať aspoň 6 znakov."),
    ]

    if form.validate_on_submit():
        username = form.username.data.strip()
        role = form.role.data

        if username_exists(username):
            flash("Toto používateľské meno už niekto používa.", "error")
        elif role == ROLE_MANAGER and not form.branch_ids.data:
            flash(
                "Manažér musí mať priradenú aspoň jednu pobočku.", "error"
            )
        else:
            if role == ROLE_ADMIN:
                create_admin(username, form.password.data)
            else:
                create_manager(
                    username, form.password.data, form.branch_ids.data
                )

            flash("Účet bol vytvorený.", "success")

            return redirect("/users")

    return render_template(
        "user_form.html", form=form, user=None, branches=branches
    )


@bp.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
def edit_user_page(user_id):
    user = _staff_user_or_none(user_id)

    if user is None:
        return "Účet neexistuje.", 404

    form = StaffUserForm()
    branches = get_branches()
    form.branch_ids.choices = [(branch[0], branch[1]) for branch in branches]
    # Rola sa pri úprave nemení (formulár ju neposiela).
    form.role.validators = [Optional()]

    if request.method == "GET":
        form.username.data = user.username
        form.role.data = user.role
        form.branch_ids.data = [branch.id for branch in user.branches]

    if form.validate_on_submit():
        username = form.username.data.strip()

        if username_exists(username, exclude_user_id=user.id):
            flash("Toto používateľské meno už niekto používa.", "error")
        elif user.role == ROLE_MANAGER and not form.branch_ids.data:
            flash(
                "Manažér musí mať priradenú aspoň jednu pobočku.", "error"
            )
        else:
            set_username(user.id, username)

            if form.password.data:
                set_password(user.id, form.password.data)

            if user.role == ROLE_MANAGER:
                set_user_branches(user.id, form.branch_ids.data)

            flash("Účet bol upravený.", "success")

            return redirect("/users")

    return render_template(
        "user_form.html", form=form, user=user, branches=branches
    )


@bp.route("/users/delete/<int:user_id>", methods=["POST"])
def delete_user_page(user_id):
    user = _staff_user_or_none(user_id)

    if user is None:
        return "Účet neexistuje.", 404

    if user.id == current_user.id:
        flash("Vlastný účet nemôžeš zmazať.", "error")

        return redirect("/users")

    if user.role == ROLE_ADMIN and count_admins() <= 1:
        flash("Nedá sa zmazať posledného administrátora.", "error")

        return redirect("/users")

    delete_user(user.id)
    flash("Účet bol zmazaný.", "success")

    return redirect("/users")
