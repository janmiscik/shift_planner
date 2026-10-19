"""Mesačný prehľad hodín (podklad pre mzdy)."""

from datetime import date
from urllib.parse import urlencode
from flask import (
    Response,
    render_template,
    request,
    send_file,
)
from app.services.report_service import (
    ABSENCE_COLUMNS,
    build_hours_csv,
    build_hours_report,
    build_hours_workbook,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _scope,
    _selected_branch_id,
)


# ---------------------------------------------------------------------
# Mesačný prehľad hodín (podklad pre mzdy)
# ---------------------------------------------------------------------

_MONTH_NAMES = [
    "január", "február", "marec", "apríl", "máj", "jún",
    "júl", "august", "september", "október", "november", "december",
]


def _report_period():
    today = date.today()

    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)

    if not 2000 <= year <= 2100:
        year = today.year

    if not 1 <= month <= 12:
        month = today.month

    return year, month


def _build_report_from_request():
    year, month = _report_period()
    only_published = request.args.get("only_published") == "1"

    return build_hours_report(
        year, month, _scope(), only_published=only_published
    )


@bp.route("/reports/hours")
def hours_report_page():
    report = _build_report_from_request()
    selected_branch = _selected_branch_id()

    query_values = {
        "year": report.year,
        "month": report.month,
        "branch_id": selected_branch,
        "only_published": "1" if report.only_published else None,
    }

    return render_template(
        "hours_report.html",
        report=report,
        absence_columns=ABSENCE_COLUMNS,
        month_names=_MONTH_NAMES,
        years=range(date.today().year - 3, date.today().year + 2),
        branches=_accessible_branches(),
        selected_branch_id=selected_branch,
        query_string=urlencode(
            {key: value for key, value in query_values.items() if value}
        ),
    )


@bp.route("/reports/hours/export.xlsx")
def hours_report_export_xlsx():
    report = _build_report_from_request()

    return send_file(
        build_hours_workbook(report),
        as_attachment=True,
        download_name=f"prehlad_hodin_{report.year}_{report.month:02d}.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


@bp.route("/reports/hours/export.csv")
def hours_report_export_csv():
    report = _build_report_from_request()

    return Response(
        build_hours_csv(report),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=prehlad_hodin_"
                f"{report.year}_{report.month:02d}.csv"
            )
        },
    )
