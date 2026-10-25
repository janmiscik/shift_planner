"""Dostupnosť zamestnancov: kedy nemôžu pracovať (vlastné oznámenie
zamestnanca; manažér ho vidí a dostane upozornenie pri plánovaní)."""

from datetime import date

from flask import flash, redirect, render_template, request
from flask_login import current_user

from app.services.availability_service import (
    WEEKDAY_EVERY,
    create_rule,
    delete_rule,
    describe_rule,
    get_rule_rows,
)
from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _scope,
    _selected_branch_id,
)


@bp.route("/my-availability", methods=["GET", "POST"])
def my_availability_page():
    """Zamestnanec: zapíše si, kedy nemôže pracovať, a prezrie/zruší
    svoje pravidlá."""

    employee_id = current_user.employee_id

    if employee_id is None:
        return redirect("/")

    if request.method == "POST":
        kind = request.form.get("kind")
        all_day = request.form.get("all_day") == "1"
        weekday = request.form.get("weekday", type=int)

        if kind not in ("weekly", "date"):
            flash("Vyber, či ide o opakované alebo jednorazové obdobie.", "error")
        elif kind == "weekly" and weekday is None:
            flash("Vyber deň v týždni.", "error")
        else:
            rule_id, error = create_rule(
                employee_id,
                weekday=weekday if kind == "weekly" else None,
                date_from=request.form.get("date_from"),
                date_to=request.form.get("date_to"),
                time_from=None if all_day else request.form.get("time_from"),
                time_to=None if all_day else request.form.get("time_to"),
                note=request.form.get("note"),
            )

            if error:
                flash(error, "error")
            else:
                flash(
                    "Zapísané. Manažér to uvidí pri plánovaní smien.",
                    "success",
                )

                return redirect("/my-availability")

    return render_template(
        "my_availability.html",
        rules=get_rule_rows(employee_id=employee_id),
        describe_rule=describe_rule,
        weekday_names=WEEKDAY_EVERY,
        today=date.today().isoformat(),
    )


@bp.route("/my-availability/<int:rule_id>/delete", methods=["POST"])
def delete_availability_rule_page(rule_id):
    employee_id = current_user.employee_id

    if employee_id is None:
        return redirect("/")

    if delete_rule(rule_id, employee_id=employee_id):
        flash("Pravidlo bolo zrušené.", "success")
    else:
        flash("Pravidlo neexistuje.", "error")

    return redirect("/my-availability")


@bp.route("/availability")
def availability_page():
    """Manažér/admin: prehľad nedostupnosti zamestnancov zo svojich
    pobočiek (len na čítanie)."""

    return render_template(
        "availability.html",
        rows=get_rule_rows(branch_ids=_scope()),
        describe_rule=describe_rule,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )
