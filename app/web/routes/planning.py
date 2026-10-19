"""Plánovanie týždňa: kopírovanie týždňa, zverejnenie a šablóny smien."""

from datetime import date, timedelta
from flask import (
    flash,
    redirect,
    render_template,
    request,
)
from flask_login import (
    current_user,
)
from app.services.audit_service import (
    record_event,
)
from app.services.publish_service import (
    publish_week,
    unpublish_week,
    week_publication_summary,
)
from app.services.week_plan_service import (
    delete_template,
    entries_from_template,
    entries_from_week,
    get_template,
    get_templates,
    parse_week_start,
    run_plan,
    save_template_from_week,
    week_label,
)
from app.services.access_service import (
    accessible_branch_ids,
)
from app.services.branch_service import (
    get_branch,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _branch_in_access,
)


# ---------------------------------------------------------------------
# Plánovanie týždňa: kopírovanie týždňa a šablóny smien
# ---------------------------------------------------------------------

def _plannable_branches():
    """Aktívne pobočky, v ktorých smie používateľ plánovať."""

    return [branch for branch in _accessible_branches() if branch[2]]


def _this_monday():
    return parse_week_start(date.today().isoformat())


def _plan_result_page(
    *,
    branch_id,
    entries,
    target,
    commit,
    title,
    description,
    form_action,
    hidden,
    back_url,
):
    rows = run_plan(entries, target, commit=commit)

    created_count = sum(1 for row in rows if row.status == "create")

    if commit:
        branch = get_branch(branch_id)

        record_event(
            "shift.bulk_create",
            (
                f"{title}: {description}, "
                f"pobočka {branch[1] if branch else branch_id} - "
                f"vytvorených {created_count}, "
                f"preskočených {len(rows) - created_count}"
            ),
            "week",
            branch_id=branch_id,
        )

    return render_template(
        "week_plan.html",
        title=title,
        description=description,
        rows=rows,
        created_count=created_count,
        skipped_count=len(rows) - created_count,
        committed=commit,
        form_action=form_action,
        hidden=hidden,
        back_url=back_url,
        target_week=week_label(target),
        calendar_url=(
            f"/calendar?year={target.year}&month={target.month}"
        ),
        publish_url=(
            f"/shifts/publish?branch_id={branch_id}"
            f"&week_date={target.isoformat()}"
        ),
    )


@bp.route("/shifts/copy-week", methods=["GET", "POST"])
def copy_week_page():
    branches = _plannable_branches()
    allowed_ids = {branch[0] for branch in branches}

    values = {
        "branch_id": request.values.get("branch_id", ""),
        "source_date": request.values.get("source_date")
        or _this_monday().isoformat(),
        "target_date": request.values.get("target_date")
        or (_this_monday() + timedelta(days=7)).isoformat(),
    }

    if request.method == "POST":
        action = request.form.get("action", "preview")
        branch_id = request.form.get("branch_id", type=int)
        source = parse_week_start(request.form.get("source_date"))
        target = parse_week_start(request.form.get("target_date"))

        if branch_id not in allowed_ids:
            flash("Vyber pobočku.", "error")
        elif source is None or target is None:
            flash(
                "Zadaj platný dátum zdrojového aj cieľového týždňa.",
                "error",
            )
        elif source == target:
            flash("Zdrojový a cieľový týždeň sú rovnaké.", "error")
        else:
            entries = entries_from_week(source, branch_id)

            if not entries:
                flash(
                    "V zdrojovom týždni nie sú v tejto pobočke žiadne "
                    "smeny.",
                    "error",
                )
            else:
                return _plan_result_page(
                    branch_id=branch_id,
                    entries=entries,
                    target=target,
                    commit=(action == "copy"),
                    title="Kopírovanie týždňa",
                    description=(
                        f"{week_label(source)} → {week_label(target)}"
                    ),
                    form_action="/shifts/copy-week",
                    hidden={
                        "branch_id": branch_id,
                        "source_date": source.isoformat(),
                        "target_date": target.isoformat(),
                    },
                    back_url="/shifts/copy-week",
                )

    return render_template(
        "copy_week.html", branches=branches, values=values
    )


@bp.route("/shifts/publish", methods=["GET", "POST"])
def publish_week_page():
    """Zverejnenie (alebo stiahnutie) rozpisu pobočky na jeden týždeň."""

    branches = _plannable_branches()
    allowed_ids = {branch[0] for branch in branches}

    source = request.form if request.method == "POST" else request.args

    branch_id = source.get("branch_id", type=int)
    monday = parse_week_start(source.get("week_date"))

    values = {
        "branch_id": branch_id,
        "week_date": (
            monday.isoformat() if monday else _this_monday().isoformat()
        ),
    }

    if request.method == "POST":
        action = request.form.get("action", "preview")

        if branch_id not in allowed_ids:
            flash("Vyber pobočku.", "error")
            branch_id = None
        elif monday is None:
            flash("Zadaj platný dátum týždňa.", "error")
        elif action == "publish":
            count = publish_week(monday, branch_id)
            flash(f"Zverejnených smien: {count}.", "success")

            return redirect(
                f"/shifts/publish?branch_id={branch_id}"
                f"&week_date={monday.isoformat()}"
            )
        elif action == "unpublish":
            count = unpublish_week(monday, branch_id)
            flash(
                f"Stiahnutých zo zverejnenia: {count}. "
                "Zamestnanci ich už nevidia.",
                "success",
            )

            return redirect(
                f"/shifts/publish?branch_id={branch_id}"
                f"&week_date={monday.isoformat()}"
            )

    summary = None

    if branch_id in allowed_ids and monday is not None:
        summary = week_publication_summary(monday, branch_id)

    return render_template(
        "publish_week.html",
        branches=branches,
        values=values,
        summary=summary,
        week=week_label(monday) if monday else "",
    )


@bp.route("/shifts/templates")
def shift_templates_page():
    return render_template(
        "shift_templates.html",
        templates=get_templates(accessible_branch_ids(current_user)),
        branches=_plannable_branches(),
        source_date=_this_monday().isoformat(),
        target_date=(_this_monday() + timedelta(days=7)).isoformat(),
    )


@bp.route("/shifts/templates/save", methods=["POST"])
def save_shift_template_page():
    allowed_ids = {branch[0] for branch in _plannable_branches()}

    branch_id = request.form.get("branch_id", type=int)
    source = parse_week_start(request.form.get("source_date"))

    if branch_id not in allowed_ids:
        flash("Vyber pobočku.", "error")
    elif source is None:
        flash("Zadaj platný dátum týždňa.", "error")
    else:
        template_id, error = save_template_from_week(
            request.form.get("name"), branch_id, source
        )

        if error:
            flash(error, "error")
        else:
            flash("Šablóna bola uložená.", "success")

    return redirect("/shifts/templates")


@bp.route("/shifts/templates/<int:template_id>/delete", methods=["POST"])
def delete_shift_template_page(template_id):
    template = get_template(template_id)

    if template is None or not _branch_in_access(template[2]):
        return "Šablóna neexistuje.", 404

    delete_template(template_id)
    flash("Šablóna bola zmazaná.", "success")

    return redirect("/shifts/templates")


@bp.route("/shifts/templates/<int:template_id>/apply", methods=["GET", "POST"])
def apply_shift_template_page(template_id):
    template = get_template(template_id)

    if template is None or not _branch_in_access(template[2]):
        return "Šablóna neexistuje.", 404

    if request.method == "GET":
        return redirect("/shifts/templates")

    action = request.form.get("action", "preview")
    target = parse_week_start(request.form.get("target_date"))

    if target is None:
        flash("Zadaj platný dátum cieľového týždňa.", "error")

        return redirect("/shifts/templates")

    entries = entries_from_template(template_id)

    if not entries:
        flash("Šablóna neobsahuje žiadne smeny.", "error")

        return redirect("/shifts/templates")

    return _plan_result_page(
        branch_id=template[2],
        entries=entries,
        target=target,
        commit=(action == "apply"),
        title=f"Šablóna „{template[1]}“",
        description=f"Použitie na týždeň {week_label(target)}",
        form_action=f"/shifts/templates/{template_id}/apply",
        hidden={"target_date": target.isoformat()},
        back_url="/shifts/templates",
    )
