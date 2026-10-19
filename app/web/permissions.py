"""Oprávnenia podľa rolí, načítanie používateľa a kontrola prihlásenia.

Admin smie všetko. Pre manažéra a zamestnanca platí zoznam POVOLENÝCH
routov - všetko ostatné je predvolene zakázané, takže nová routa je
pre nich automaticky neprístupná, kým ju sem niekto výslovne nepridá.
"""

import os
from flask import (
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    current_user,
)
from app.extensions import login_manager
from app.orm_models import ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_MANAGER
from app.services.leave_request_service import (
    pending_count as pending_leave_request_count,
)
from app.services.access_service import (
    accessible_branch_ids,
)
from app.services.auth_service import (
    get_user_by_id,
)
from app.services.shift_service import (
    auto_publish_enabled,
)

from app.web.core import bp


# Routy dostupné bez prihlásenia (len prihlasovacia stránka a
# statické súbory).
_PUBLIC_ENDPOINTS = {"main.login_page", "static"}

# Oprávnenia podľa rolí. Admin smie všetko. Pre manažéra a
# zamestnanca platí zoznam POVOLENÝCH routov - všetko ostatné je
# predvolene zakázané, takže nová routa je pre nich automaticky
# neprístupná, kým ju sem niekto výslovne nepridá (bezpečnejšie ako
# opačný zoznam zákazov).

# Routy pre každého prihláseného používateľa.
_COMMON_ENDPOINTS = {
    "main.logout_page",
    "main.change_password_page",
}

# Vlastný rozpis a jeho export (zamestnanec; aj manažér, ktorý je
# zároveň zamestnancom - má jeden účet s oboma funkciami).
_OWN_SCHEDULE_ENDPOINTS = {
    "main.my_schedule",
    "main.my_schedule_export_ics",
    "main.my_schedule_export_xlsx",
    "main.my_schedule_export_pdf",
    "main.my_requests_page",
    "main.cancel_my_request_page",
}

# Zamestnanec: vlastný rozpis a jeho export.
_EMPLOYEE_ALLOWED_ENDPOINTS = _COMMON_ENDPOINTS | _OWN_SCHEDULE_ENDPOINTS

# Zamestnanec: kalendár svojej pobočky len na čítanie (a jeho
# exporty). Dá sa vypnúť cez EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR=0.
_EMPLOYEE_BRANCH_VIEW_ENDPOINTS = {
    "main.calendar_page",
    "main.api_shifts",
    "main.export_calendar_xlsx",
    "main.export_calendar_pdf",
    "main.export_calendar_ics",
}

# Manažér: plánovanie smien a absencií na pridelených pobočkách,
# prehľad zamestnancov a oddelení len na čítanie. Pobočky, účty,
# zamestnancov, oddelenia a zálohy spravuje len admin.
_MANAGER_ALLOWED_ENDPOINTS = (
    _COMMON_ENDPOINTS
    | _EMPLOYEE_BRANCH_VIEW_ENDPOINTS
    | {
        "main.home",
        "main.employees_page",
        "main.export_employee_shifts_ics",
        "main.departments_page",
        "main.shifts_page",
        "main.export_shifts_xlsx",
        "main.export_shifts_pdf",
        "main.export_shifts_ics",
        "main.add_shift_page",
        "main.edit_shift_page",
        "main.delete_shift_page",
        "main.copy_week_page",
        "main.publish_week_page",
        "main.shift_templates_page",
        "main.save_shift_template_page",
        "main.delete_shift_template_page",
        "main.apply_shift_template_page",
        "main.hours_report_page",
        "main.hours_report_export_xlsx",
        "main.hours_report_export_csv",
        "main.audit_page",
        "main.audit_export_csv",
        "main.leave_requests_page",
        "main.approve_leave_request_page",
        "main.reject_leave_request_page",
        "main.absences_page",
        "main.add_absence_page",
        "main.edit_absence_page",
        "main.delete_absence_page",
        "main.api_employee_departments",
        "main.api_move_shift",
    }
)


def _employees_can_view_branch_calendar():
    # Predvolene VYPNUTÉ: zamestnanec vidí len svoje smeny. Zapnúť sa
    # dá cez EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR=1 (kalendár pobočky len
    # na čítanie).
    return os.environ.get("EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR", "0") == "1"


@login_manager.user_loader
def _load_user(user_id):
    return get_user_by_id(user_id)


@bp.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None

    if not current_user.is_authenticated:
        return redirect(url_for("main.login_page", next=request.path))

    endpoint = request.endpoint

    if endpoint is None or current_user.role == ROLE_ADMIN:
        return None

    if current_user.role == ROLE_MANAGER:
        allowed = endpoint in _MANAGER_ALLOWED_ENDPOINTS or (
            current_user.employee_id is not None
            and endpoint in _OWN_SCHEDULE_ENDPOINTS
        )
    elif current_user.role == ROLE_EMPLOYEE:
        allowed = endpoint in _EMPLOYEE_ALLOWED_ENDPOINTS or (
            endpoint in _EMPLOYEE_BRANCH_VIEW_ENDPOINTS
            and _employees_can_view_branch_calendar()
        )
    else:
        allowed = False

    if not allowed:
        return render_template("forbidden.html"), 403

    return None


@bp.app_context_processor
def _inject_template_flags():
    pending_requests = 0

    if current_user.is_authenticated and current_user.role in (
        ROLE_ADMIN,
        ROLE_MANAGER,
    ):
        pending_requests = pending_leave_request_count(
            accessible_branch_ids(current_user)
        )

    return {
        "employees_can_view_calendar": _employees_can_view_branch_calendar(),
        "auto_publish_shifts": auto_publish_enabled(),
        "pending_leave_requests": pending_requests,
    }
