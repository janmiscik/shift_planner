"""Vlastný rozpis zamestnanca a jeho exporty."""

from flask import (
    redirect,
    render_template,
    send_file,
)
from flask_login import (
    current_user,
)
from app.services.employee_service import (
    get_employee,
)
from app.services.shift_service import (
    get_shifts_by_employee,
)
from app.services.absence_service import (
    get_absences_by_employee,
)
from app.services.export_service import (
    build_shifts_ics,
    build_shifts_pdf,
    build_shifts_workbook,
)

from app.web.core import bp


@bp.route("/my-schedule")
def my_schedule():
    if current_user.employee_id is None:
        return redirect("/")

    employee = get_employee(current_user.employee_id)
    shifts = get_shifts_by_employee(
        current_user.employee_id, published_only=True
    )
    absences = get_absences_by_employee(current_user.employee_id)

    return render_template(
        "my_schedule.html",
        employee=employee,
        shifts=shifts,
        absences=absences,
    )


@bp.route("/my-schedule/export.ics")
def my_schedule_export_ics():
    if current_user.employee_id is None:
        return redirect("/")

    employee = get_employee(current_user.employee_id)
    shifts = get_shifts_by_employee(
        current_user.employee_id, published_only=True
    )
    calendar_name = f"Moje smeny - {employee[1]} {employee[2]}"

    ics = build_shifts_ics(shifts, calendar_name=calendar_name)

    return send_file(
        ics,
        as_attachment=True,
        download_name=f"moje_smeny_{employee[1]}_{employee[2]}.ics",
        mimetype="text/calendar",
    )


def _own_schedule_or_none():
    """(zamestnanec, smeny) prihláseného zamestnanca, inak ``None``."""

    if current_user.employee_id is None:
        return None

    employee = get_employee(current_user.employee_id)

    if employee is None:
        return None

    return employee, get_shifts_by_employee(
        current_user.employee_id, published_only=True
    )


@bp.route("/my-schedule/export.xlsx")
def my_schedule_export_xlsx():
    own = _own_schedule_or_none()

    if own is None:
        return redirect("/")

    employee, shifts = own
    workbook = build_shifts_workbook(
        shifts, title=f"Moje smeny - {employee[1]} {employee[2]}"
    )

    return send_file(
        workbook,
        as_attachment=True,
        download_name=f"moje_smeny_{employee[1]}_{employee[2]}.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


@bp.route("/my-schedule/export.pdf")
def my_schedule_export_pdf():
    own = _own_schedule_or_none()

    if own is None:
        return redirect("/")

    employee, shifts = own
    pdf = build_shifts_pdf(
        shifts,
        title=f"Moje smeny - {employee[1]} {employee[2]}",
    )

    return send_file(
        pdf,
        as_attachment=True,
        download_name=f"moje_smeny_{employee[1]}_{employee[2]}.pdf",
        mimetype="application/pdf",
    )
