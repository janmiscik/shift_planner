"""Kalendár, jeho exporty a JSON API pre formuláre a FullCalendar."""

from datetime import date
from flask import (
    jsonify,
    render_template,
    request,
    send_file,
)
from flask_login import (
    current_user,
)
from app.orm_models import ROLE_EMPLOYEE
from app.services.publish_service import (
    get_draft_shift_ids,
)
from app.services.employee_service import (
    get_employees,
)
from app.services.department_service import (
    get_departments,
)
from app.services.employee_department_service import (
    get_employee_departments,
)
from app.services.shift_service import (
    filter_shifts_by_department,
    get_shift,
    get_shifts_in_range,
    move_shift,
    validate_shift,
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

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _employee_in_access,
    _scope,
    _selected_branch_id,
)


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

    scope = _scope()

    employees = get_employees(scope)
    departments = get_departments(scope)

    calendar_days = build_calendar_days(year, month)

    first_day, last_day = month_bounds(year, month)
    understaffed_shifts = get_understaffed_shifts(
        first_day, last_day, scope
    )

    if current_user.role == ROLE_EMPLOYEE:
        understaffed_shifts = []

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
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
        read_only=current_user.role == ROLE_EMPLOYEE,
    )


@bp.route("/calendar/export.xlsx")
def export_calendar_xlsx():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    first_day, last_day = month_bounds(year, month)
    shifts = get_shifts_in_range(
        first_day,
        last_day,
        _scope(),
        published_only=current_user.role == ROLE_EMPLOYEE,
    )

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
    shifts = get_shifts_in_range(
        first_day,
        last_day,
        _scope(),
        published_only=current_user.role == ROLE_EMPLOYEE,
    )

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
    shifts = get_shifts_in_range(
        first_day,
        last_day,
        _scope(),
        published_only=current_user.role == ROLE_EMPLOYEE,
    )

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
    if not _employee_in_access(employee_id):
        return jsonify([]), 404

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

    is_employee_view = current_user.role == ROLE_EMPLOYEE

    shifts = get_shifts_in_range(
        first_day,
        last_day,
        _scope(),
        published_only=is_employee_view,
    )

    if employee_id:
        shifts = [
            shift for shift in shifts if shift[7] == employee_id
        ]

    if department_id:
        shifts = filter_shifts_by_department(shifts, department_id)

    # Koncepty sa v kalendári odlišujú len manažérovi/adminovi;
    # zamestnanec vidí iba zverejnené smeny (bez rozlišovania).
    draft_ids = (
        None
        if is_employee_view
        else get_draft_shift_ids(first_day, last_day, _scope())
    )

    events = shifts_to_fullcalendar_events(shifts, draft_ids)

    if current_user.role == ROLE_EMPLOYEE:
        # Zamestnanec vidí rozpis pobočky, ale nie stav týždenných
        # fondov hodín kolegov.
        for event in events:
            event["extendedProps"]["weeklyHours"] = None
            event["className"] = " ".join(
                name
                for name in event["className"].split()
                if not name.startswith("hours-")
            )

    return jsonify(events)


@bp.route("/api/shifts/<int:shift_id>/move", methods=["POST"])
def api_move_shift(shift_id):
    """Presunie smenu po drag-and-drop v kalendári (FullCalendar)."""

    payload = request.get_json(silent=True) or {}

    shift = get_shift(shift_id)

    if shift is None or not _employee_in_access(shift[1]):
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
