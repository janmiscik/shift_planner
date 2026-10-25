"""Výmena smien medzi kolegami (navrhnutie, odpoveď kolegu, schválenie
manažérom)."""

from flask import flash, redirect, render_template, request
from flask_login import current_user

from app.services.shift_swap_service import (
    STATUS_LABELS,
    STATUS_PENDING_COLLEAGUE,
    STATUS_PENDING_MANAGER,
    approve_swap,
    cancel_swap,
    candidate_colleagues,
    colleague_respond,
    create_swap,
    get_swap,
    get_swaps,
    reject_swap,
    swappable_shifts,
)
from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _branch_in_access,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Zamestnanec
# ---------------------------------------------------------------------

@bp.route("/my-swaps", methods=["GET", "POST"])
def my_swaps_page():
    """Zamestnanec: navrhnutie výmeny, žiadosti čakajúce na jeho odpoveď
    a história."""

    employee_id = current_user.employee_id

    if employee_id is None:
        return redirect("/")

    if request.method == "POST":
        swap_id, error = create_swap(
            employee_id,
            request.form.get("colleague_id", type=int),
            request.form.get("shift_id", type=int),
            wants_swap=request.form.get("wants_swap") == "1",
            note=request.form.get("note"),
        )

        if error:
            flash(error, "error")
        else:
            flash(
                "Žiadosť bola odoslaná kolegovi. Ak ju prijme, rozhodne "
                "o nej manažér.",
                "success",
            )

            return redirect("/my-swaps")

    all_swaps = get_swaps(employee_id=employee_id)

    incoming = [
        row
        for row in all_swaps
        if row[4] == employee_id and row[13] == STATUS_PENDING_COLLEAGUE
    ]
    history = [row for row in all_swaps if row not in incoming]

    return render_template(
        "my_swaps.html",
        my_shifts=swappable_shifts(employee_id),
        colleagues=candidate_colleagues(employee_id),
        incoming=incoming,
        swaps=history,
        status_labels=STATUS_LABELS,
        employee_id=employee_id,
        open_statuses=(STATUS_PENDING_COLLEAGUE, STATUS_PENDING_MANAGER),
    )


@bp.route("/my-swaps/<int:swap_id>/respond", methods=["POST"])
def respond_swap_page(swap_id):
    employee_id = current_user.employee_id

    if employee_id is None:
        return redirect("/")

    action = request.form.get("action")

    if action not in ("accept", "decline"):
        flash("Vyber, či žiadosť prijímaš alebo odmietaš.", "error")

        return redirect("/my-swaps")

    ok, error = colleague_respond(
        swap_id,
        employee_id,
        accept=action == "accept",
        counter_shift_id=request.form.get("counter_shift_id", type=int),
    )

    if not ok:
        flash(error, "error")
    elif action == "accept":
        flash(
            "Žiadosť si prijal. Teraz ju musí schváliť manažér - dovtedy "
            "sa smeny nemenia.",
            "success",
        )
    else:
        flash("Žiadosť si odmietol.", "success")

    return redirect("/my-swaps")


@bp.route("/my-swaps/<int:swap_id>/cancel", methods=["POST"])
def cancel_swap_page(swap_id):
    employee_id = current_user.employee_id

    if employee_id is None:
        return redirect("/")

    ok, error = cancel_swap(swap_id, employee_id)

    if ok:
        flash("Žiadosť bola zrušená.", "success")
    else:
        flash(error, "error")

    return redirect("/my-swaps")


# ---------------------------------------------------------------------
# Manažér / admin
# ---------------------------------------------------------------------

@bp.route("/swap-requests")
def swap_requests_page():
    status = request.args.get("status", STATUS_PENDING_MANAGER)

    if status not in STATUS_LABELS and status != "all":
        status = STATUS_PENDING_MANAGER

    return render_template(
        "swap_requests.html",
        swaps=get_swaps(
            branch_ids=_scope(), status=None if status == "all" else status
        ),
        status=status,
        status_labels=STATUS_LABELS,
        own_employee_id=current_user.employee_id,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


def _decide_swap(swap_id, decision):
    swap = get_swap(swap_id)

    if swap is None or not _branch_in_access(swap[7]):
        return "Žiadosť neexistuje.", 404

    own = current_user.employee_id

    if own is not None and own in (swap[1], swap[4]):
        flash(
            "Výmenu, v ktorej si zainteresovaný, nemôžeš vybaviť sám - "
            "požiadaj iného manažéra alebo admina.",
            "error",
        )

        return redirect("/swap-requests")

    note = request.form.get("decision_note")

    if decision == "approve":
        ok, messages = approve_swap(swap_id, current_user.id, note)

        if not ok:
            flash("Výmenu nemožno schváliť:", "error")

            for message in messages:
                flash(message, "error")

            return redirect("/swap-requests")

        flash("Výmena bola schválená a smeny sú prehodené.", "success")

        for message in messages:
            flash(f"Pozor: {message}", "warning")

        return redirect("/swap-requests")

    ok, error = reject_swap(swap_id, current_user.id, note)

    if ok:
        flash("Žiadosť bola zamietnutá.", "success")
    else:
        flash(error, "error")

    return redirect("/swap-requests")


@bp.route("/swap-requests/<int:swap_id>/approve", methods=["POST"])
def approve_swap_page(swap_id):
    return _decide_swap(swap_id, "approve")


@bp.route("/swap-requests/<int:swap_id>/reject", methods=["POST"])
def reject_swap_page(swap_id):
    return _decide_swap(swap_id, "reject")
