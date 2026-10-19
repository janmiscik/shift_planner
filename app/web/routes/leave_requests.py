"""Žiadosti o voľno (podanie zamestnancom, schválenie manažérom)."""

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
from app.services.leave_request_service import (
    STATUS_LABELS,
    STATUS_PENDING,
    approve_request as approve_leave_request,
    cancel_request as cancel_leave_request,
    conflicting_shift_count,
    create_request,
    day_count,
    get_request as get_leave_request,
    get_requests as get_leave_requests,
    reject_request as reject_leave_request,
)
from app.web.forms import (
    LeaveRequestForm,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _branch_in_access,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Žiadosti o voľno
# ---------------------------------------------------------------------

@bp.route("/my-requests", methods=["GET", "POST"])
def my_requests_page():
    """Zamestnanec: podanie žiadosti o voľno a prehľad vlastných
    žiadostí."""

    if current_user.employee_id is None:
        return redirect("/")

    form = LeaveRequestForm()

    is_valid = form.validate_on_submit()

    if form.absence_type.errors:
        # Zrozumiteľná správa namiesto anglického "Not a valid choice".
        form.absence_type.errors = ["Vyber druh voľna zo zoznamu."]

    if is_valid:
        request_id, error = create_request(
            current_user.employee_id,
            form.absence_type.data,
            form.start_date.data.isoformat(),
            form.end_date.data.isoformat(),
            form.note.data,
        )

        if error:
            flash(error, "error")
        else:
            flash(
                "Žiadosť bola odoslaná - čaká na schválenie manažérom.",
                "success",
            )

            return redirect("/my-requests")

    return render_template(
        "my_requests.html",
        form=form,
        requests=get_leave_requests(employee_id=current_user.employee_id),
        status_labels=STATUS_LABELS,
        day_count=day_count,
        today=date.today().isoformat(),
    )


@bp.route("/my-requests/<int:request_id>/cancel", methods=["POST"])
def cancel_my_request_page(request_id):
    if current_user.employee_id is None:
        return redirect("/")

    ok, error = cancel_leave_request(request_id, current_user.employee_id)

    if ok:
        flash("Žiadosť bola zrušená.", "success")
    else:
        flash(error, "error")

    return redirect("/my-requests")


@bp.route("/leave-requests")
def leave_requests_page():
    """Manažér/admin: žiadosti zamestnancov z jeho pobočiek."""

    status = request.args.get("status", STATUS_PENDING)

    if status not in STATUS_LABELS and status != "all":
        status = STATUS_PENDING

    requests_list = get_leave_requests(
        branch_ids=_scope(),
        status=None if status == "all" else status,
    )

    shift_conflicts = {
        row[0]: conflicting_shift_count(row[1], row[7], row[8])
        for row in requests_list
        if row[10] == STATUS_PENDING
    }

    return render_template(
        "leave_requests.html",
        requests=requests_list,
        status=status,
        status_labels=STATUS_LABELS,
        shift_conflicts=shift_conflicts,
        day_count=day_count,
        own_employee_id=current_user.employee_id,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


def _decide_leave_request(request_id, decision):
    leave_request = get_leave_request(request_id)

    if leave_request is None or not _branch_in_access(leave_request[4]):
        return "Žiadosť neexistuje.", 404

    if (
        current_user.employee_id is not None
        and leave_request[1] == current_user.employee_id
    ):
        flash(
            "Vlastnú žiadosť nemôžeš vybaviť sám - požiadaj iného "
            "manažéra alebo admina.",
            "error",
        )

        return redirect("/leave-requests")

    note = request.form.get("decision_note")

    if decision == "approve":
        ok, error = approve_leave_request(request_id, current_user.id, note)
    else:
        ok, error = reject_leave_request(request_id, current_user.id, note)

    if not ok:
        flash(error, "error")

        return redirect("/leave-requests")

    if decision == "approve":
        conflicts = conflicting_shift_count(
            leave_request[1], leave_request[7], leave_request[8]
        )
        message = "Žiadosť bola schválená a voľno zapísané do absencií."

        if conflicts:
            message += (
                f" Pozor: zamestnanec má v tomto období ešte "
                f"{conflicts} naplánovaných smien - skontroluj ich na "
                "stránke Smeny."
            )

        flash(message, "success")
    else:
        flash("Žiadosť bola zamietnutá.", "success")

    return redirect("/leave-requests")


@bp.route("/leave-requests/<int:request_id>/approve", methods=["POST"])
def approve_leave_request_page(request_id):
    return _decide_leave_request(request_id, "approve")


@bp.route("/leave-requests/<int:request_id>/reject", methods=["POST"])
def reject_leave_request_page(request_id):
    return _decide_leave_request(request_id, "reject")
