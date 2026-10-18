"""Webová aplikácia Shift Planner.

Táto vrstva sa stará výlučne o spracovanie HTTP požiadaviek
(parsovanie vstupov, presmerovania, JSON odpovede). Biznis logika
a výpočty (kolízie smien, týždenné fondy, kalendárna mriežka,
exporty) žijú v ``app/services``.

Role: admin (všetko), manager (smeny a absencie na pridelených
pobočkách), employee (vlastný rozpis a export). Oprávnenia routov
určujú zoznamy ``_*_ALLOWED_ENDPOINTS`` nižšie; rozsah dát podľa
pobočiek rieši ``app/services/access_service.py``.
"""

import os
from datetime import date, timedelta
from urllib.parse import urlencode
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf import CSRFProtect
from wtforms.validators import DataRequired, Length, Optional

from app.data.database import (
    is_sqlite_uri,
    resolve_database_uri,
    sqlite_path_from_uri,
)
from app.extensions import db, login_manager, migrate

load_dotenv()

from app.orm_models import ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_MANAGER

from app.services.audit_service import (
    ADMIN_ONLY_ENTITIES,
    ENTITY_LABELS,
    entries_to_csv,
    get_audit_entries,
    get_audit_usernames,
    init_audit,
    record_event,
    to_local,
)
from app.services.login_throttle import LoginThrottle
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
    pending_count as pending_leave_request_count,
    reject_request as reject_leave_request,
)
from app.services.publish_service import (
    get_draft_shift_ids,
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
    employee_branch_id,
    resolve_scope,
    shift_branch_id,
    absence_branch_id,
)

from app.services.branch_service import (
    add_branch,
    get_branch,
    get_branches,
    get_branches_with_counts,
    get_default_branch_id,
    set_branch_active,
    update_branch,
)

from app.services.auth_service import (
    change_own_password,
    count_admins,
    create_admin,
    create_employee_user,
    create_manager,
    delete_user,
    get_staff_users,
    get_user_by_employee_id,
    get_user_by_id,
    authenticate,
    set_manager_function,
    set_password,
    set_user_branches,
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
    add_department,
    get_department,
    get_departments,
    set_department_active,
    update_department,
)

from app.services.employee_department_service import (
    add_employee_department,
    can_add_employee_department,
    can_update_employee_department,
    delete_employee_department,
    get_employee_department,
    get_employee_department_hours,
    get_employee_department_ids,
    get_employee_departments,
    update_employee_department,
)

from app.services.shift_service import (
    add_shift,
    delete_shift,
    filter_shifts_by_department,
    get_shift,
    get_shifts,
    auto_publish_enabled,
    get_shifts_by_employee,
    get_shifts_in_range,
    move_shift,
    update_shift,
    validate_shift,
)

from app.services.absence_service import (
    add_absence,
    delete_absence,
    get_absence,
    get_absences,
    get_absences_by_employee,
    update_absence,
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

from app.web.forms import (
    AbsenceForm,
    BranchForm,
    ChangePasswordForm,
    CredentialsForm,
    DepartmentForm,
    EmployeeDepartmentForm,
    EmployeeForm,
    LeaveRequestForm,
    LoginForm,
    ShiftForm,
    StaffUserForm,
)


bp = Blueprint("main", __name__)

csrf = CSRFProtect()

_DEFAULT_SECRET_KEY = "dev-docasny-kluc-zmen-v-produkcii"

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


def _scope():
    """Rozsah pobočiek pre zoznamy a exporty: celý prístupný rozsah,
    alebo jedna pobočka vybraná cez ``?branch_id=`` (ak na ňu
    používateľ má právo). ``None`` = bez obmedzenia (admin)."""

    return resolve_scope(
        current_user, request.args.get("branch_id", type=int)
    )


def _selected_branch_id():
    """Pobočka vybraná cez ``?branch_id=``, ak je platná a prístupná
    (inak ``None`` = všetky prístupné)."""

    requested = request.args.get("branch_id", type=int)

    if requested is None:
        return None

    allowed = accessible_branch_ids(current_user)

    if allowed is None or requested in allowed:
        return requested

    return None


def _accessible_branches():
    """Pobočky, ktoré smie používateľ vidieť (pre výber v UI)."""

    return get_branches(accessible_branch_ids(current_user))


def _branch_in_access(branch_id):
    allowed = accessible_branch_ids(current_user)

    return allowed is None or branch_id in allowed


def _employee_in_access(employee_id):
    return _branch_in_access(employee_branch_id(employee_id))


def _active_department_choices(branch_ids=None):
    return [
        (department[0], department[1])
        for department in get_departments(branch_ids)
        if department[2]
    ]


def _active_employee_choices(branch_ids=None):
    return [
        (employee[0], f"{employee[1]} {employee[2]} — {employee[3]}")
        for employee in get_employees(branch_ids)
        if employee[5]
    ]


def _branch_form_choices():
    """Voľby pre výber pobočky vo formulároch (len aktívne pobočky;
    prázdna hodnota = predvolená pobočka)."""

    branches = get_branches(only_active=True)

    return branches, [("", "")] + [
        (str(branch[0]), branch[1]) for branch in branches
    ]


def _resolve_branch_id(raw_value):
    if raw_value:
        return int(raw_value)

    return get_default_branch_id()


def _is_safe_redirect_target(target):
    """Overí, že ``next`` po prihlásení mieri len na vlastnú stránku
    appky (relatívna cesta), nie na cudziu doménu - inak by sa dal
    vytvoriť odkaz typu ``/login?next=https://podvodnik.sk``, ktorý by
    po legitímnom prihlásení presmeroval používateľa na podvodnú
    stránku (tzv. open redirect)."""

    if not target or not isinstance(target, str):
        return False

    # "//domena.sk" (protocol-relative) a "/\domena.sk" (prehliadače
    # berú spätné lomítko ako obyčajné) by mierili na cudziu doménu.
    if not target.startswith("/") or target.startswith("//"):
        return False

    if "\\" in target:
        return False

    parsed = urlparse(target)

    return not parsed.scheme and not parsed.netloc


def _too_many_attempts_message(wait_seconds):
    minutes = max(1, -(-wait_seconds // 60))

    return (
        "Príliš veľa neúspešných pokusov o prihlásenie. "
        f"Skús to znova o {minutes} min."
    )


@bp.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        if current_user.role == "employee":
            return redirect("/my-schedule")

        return redirect("/")

    form = LoginForm()

    if form.validate_on_submit():
        throttle = current_app.extensions["login_throttle"]
        client_ip = request.remote_addr or "unknown"
        username_key = form.username.data.strip().lower()

        wait_seconds = throttle.retry_after(client_ip, username_key)

        if wait_seconds > 0:
            current_app.logger.warning(
                "Prihlásenie zablokované (príliš veľa pokusov): "
                "user=%r ip=%s",
                username_key[:80],
                client_ip,
            )
            flash(_too_many_attempts_message(wait_seconds), "error")

            return render_template("login.html", form=form), 429

        user = authenticate(form.username.data, form.password.data)

        if user is None:
            throttle.record_failure(client_ip, username_key)
            current_app.logger.warning(
                "Neúspešné prihlásenie: user=%r ip=%s",
                username_key[:80],
                client_ip,
            )
            flash("Nesprávne používateľské meno alebo heslo.", "error")
        else:
            throttle.record_success(client_ip, username_key)
            login_user(user)

            if user.role == "employee":
                return redirect("/my-schedule")

            next_url = request.args.get("next")

            if _is_safe_redirect_target(next_url):
                return redirect(next_url)

            return redirect("/")

    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout_page():
    logout_user()

    return redirect("/login")


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


@bp.route("/account/password", methods=["GET", "POST"])
def change_password_page():
    """Zmena vlastného hesla (každá rola)."""

    form = ChangePasswordForm()

    if form.validate_on_submit():
        # Aj tu sa limituje hádanie (napr. pri odcudzenej session) -
        # rovnakým mechanizmom ako pri prihlásení.
        throttle = current_app.extensions["login_throttle"]
        client_ip = request.remote_addr or "unknown"
        throttle_key = f"zmena-hesla:{current_user.id}"

        wait_seconds = throttle.retry_after(client_ip, throttle_key)

        if wait_seconds > 0:
            flash(_too_many_attempts_message(wait_seconds), "error")

            return (
                render_template("change_password.html", form=form),
                429,
            )

        changed = change_own_password(
            current_user.id,
            form.current_password.data,
            form.new_password.data,
        )

        if not changed:
            throttle.record_failure(client_ip, throttle_key)

        if changed:
            throttle.record_success(client_ip, throttle_key)
            flash("Heslo bolo zmenené.", "success")

            if current_user.role == ROLE_EMPLOYEE:
                return redirect("/my-schedule")

            return redirect("/")

        flash("Aktuálne heslo nie je správne.", "error")

    return render_template("change_password.html", form=form)


@bp.route("/")
def home():
    scope = _scope()

    employees = get_employees(scope)
    shifts = get_shifts(scope)

    return render_template(
        "dashboard.html",
        employees=employees,
        shifts=shifts,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/admin/backup")
def admin_backup():
    """Stiahne aktuálnu databázu ako súbor (a zároveň ju zazálohuje
    aj do priečinka ``backups/`` na serveri)."""

    from flask import current_app

    from app.services.backup_service import (
        BackupError,
        create_backup,
        create_postgres_backup,
    )

    database_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]

    if not is_sqlite_uri(database_uri):
        try:
            backup_path = create_postgres_backup(
                database_uri, label="stiahnutie"
            )
        except BackupError as error:
            return f"Zálohu sa nepodarilo vytvoriť: {error}", 500

        record_event(
            "backup.download",
            f"Stiahnutá záloha databázy ({backup_path.name})",
            "system",
        )

        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_path.name,
            mimetype="application/octet-stream",
        )

    database_path = sqlite_path_from_uri(database_uri)

    backup_path = create_backup(database_path, label="stiahnutie")

    if backup_path is None:
        return "Databáza ešte neexistuje, niet čo zálohovať.", 404

    record_event(
        "backup.download",
        f"Stiahnutá záloha databázy ({backup_path.name})",
        "system",
    )

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name,
        mimetype="application/x-sqlite3",
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


# ---------------------------------------------------------------------
# Oddelenia
# ---------------------------------------------------------------------

@bp.route("/departments")
def departments_page():
    departments = get_departments(_scope())

    return render_template(
        "departments.html",
        departments=departments,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/departments/add", methods=["GET", "POST"])
def add_department_page():
    form = DepartmentForm()
    branches, form.branch_id.choices = _branch_form_choices()

    if form.validate_on_submit():
        branch_id = _resolve_branch_id(form.branch_id.data)

        if branch_id is None:
            flash("Nie je dostupná žiadna aktívna pobočka.", "error")

            return render_template(
                "add_department.html", form=form, branches=branches
            )

        department_id = add_department(
            form.name.data,
            form.min_staff.data,
            branch_id=branch_id,
        )

        if department_id is None:
            flash(
                "Oddelenie s týmto názvom už existuje v tejto pobočke.",
                "error",
            )

            return render_template(
                "add_department.html", form=form, branches=branches
            )

        return redirect("/departments")

    return render_template(
        "add_department.html", form=form, branches=branches
    )


@bp.route("/departments/edit/<int:department_id>", methods=["GET", "POST"])
def edit_department_page(department_id):
    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    form = DepartmentForm()

    if request.method == "GET":
        form.name.data = department[1]
        form.min_staff.data = department[3]

    if form.validate_on_submit():
        updated = update_department(
            department_id, form.name.data, form.min_staff.data
        )

        if not updated:
            flash(
                "Oddelenie s týmto názvom už existuje v tejto pobočke.",
                "error",
            )

            return render_template(
                "edit_department.html",
                department=department,
                form=form,
            )

        return redirect("/departments")

    return render_template(
        "edit_department.html",
        department=department,
        form=form,
    )


@bp.route("/departments/toggle/<int:department_id>", methods=["POST"])
def toggle_department_page(department_id):
    department = get_department(department_id)

    if department is None:
        return "Oddelenie neexistuje.", 404

    new_status = 0 if department[2] else 1
    set_department_active(department_id, new_status)

    return redirect("/departments")


# ---------------------------------------------------------------------
# Smeny
# ---------------------------------------------------------------------

@bp.route("/shifts")
def shifts_page():
    scope = _scope()

    shifts = get_shifts(scope)

    return render_template(
        "shifts.html",
        shifts=shifts,
        draft_ids=get_draft_shift_ids(branch_ids=scope),
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/shifts/export.xlsx")
def export_shifts_xlsx():
    shifts = get_shifts(_scope())
    workbook = build_shifts_workbook(shifts, title="Všetky smeny")

    return send_file(
        workbook,
        as_attachment=True,
        download_name="smeny.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


@bp.route("/shifts/export.pdf")
def export_shifts_pdf():
    shifts = get_shifts(_scope())
    pdf = build_shifts_pdf(shifts, title="Harmonogram smien - všetky")

    return send_file(
        pdf,
        as_attachment=True,
        download_name="smeny.pdf",
        mimetype="application/pdf",
    )


@bp.route("/shifts/export.ics")
def export_shifts_ics():
    shifts = get_shifts(_scope())
    ics = build_shifts_ics(shifts, calendar_name="Shift Planner - všetky smeny")

    return send_file(
        ics,
        as_attachment=True,
        download_name="smeny.ics",
        mimetype="text/calendar",
    )


@bp.route("/shifts/add", methods=["GET", "POST"])
def add_shift_page():
    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices(access)
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name)
        for dep_id, name in _active_department_choices(access)
    ]

    if request.method == "GET":
        selected_date = request.args.get("date", "")

        if selected_date:
            try:
                form.shift_date.data = date.fromisoformat(selected_date)
            except ValueError:
                pass

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=selected_date,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=request.form.get("shift_date", ""),
            form=form,
        )

    employee_id = form.employee_id.data
    shift_date = form.shift_date.data.isoformat()
    start_time = form.start_time.data.strftime("%H:%M")
    end_time = form.end_time.data.strftime("%H:%M")
    shift_type = form.shift_type.data
    department_id = form.department_id.data or None

    employee = get_employee(employee_id)

    if employee is None or not employee[5]:
        flash("Neaktívnemu zamestnancovi nie je možné pridať smenu.", "error")

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=shift_date,
            form=form,
        )

    errors = validate_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        department_id=department_id,
    )

    if errors:
        for error in errors:
            flash(error, "error")

        return render_template(
            "add_shift.html",
            employees=employees,
            selected_date=shift_date,
            form=form,
        )

    add_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        shift_type,
        department_id=department_id,
    )

    return redirect("/shifts")


@bp.route("/shifts/edit/<int:shift_id>", methods=["GET", "POST"])
def edit_shift_page(shift_id):
    shift = get_shift(shift_id)

    if shift is None or not _employee_in_access(shift[1]):
        return "Smena neexistuje.", 404

    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = ShiftForm()
    form.employee_id.choices = _active_employee_choices(access)
    form.department_id.choices = [("", "-- Bez oddelenia --")] + [
        (str(dep_id), name)
        for dep_id, name in _active_department_choices(access)
    ]

    if request.method == "GET":
        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    employee_id = form.employee_id.data
    shift_date = form.shift_date.data.isoformat()
    start_time = form.start_time.data.strftime("%H:%M")
    end_time = form.end_time.data.strftime("%H:%M")
    shift_type = form.shift_type.data
    department_id = form.department_id.data or None

    errors = validate_shift(
        employee_id,
        shift_date,
        start_time,
        end_time,
        department_id=department_id,
        exclude_shift_id=shift_id,
    )

    if errors:
        for error in errors:
            flash(error, "error")

        return render_template(
            "edit_shift.html",
            shift=shift,
            employees=employees,
            form=form,
        )

    update_shift(
        shift_id,
        employee_id,
        shift_date,
        start_time,
        end_time,
        shift_type,
        department_id=department_id,
    )

    return redirect("/shifts")


@bp.route("/shifts/delete/<int:shift_id>", methods=["POST"])
def delete_shift_page(shift_id):
    if not _branch_in_access(shift_branch_id(shift_id)):
        return "Smena neexistuje.", 404

    delete_shift(shift_id)

    return redirect("/shifts")


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


# ---------------------------------------------------------------------
# Záznamy zmien (audit log)
# ---------------------------------------------------------------------

_AUDIT_PAGE_SIZE = 50
_AUDIT_EXPORT_LIMIT = 10000


def _parse_iso_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _audit_entity_labels():
    """Typy záznamov, ktoré smie používateľ filtrovať (záznamy o
    účtoch, pobočkách a systéme vidí len admin)."""

    if current_user.role == ROLE_ADMIN:
        return ENTITY_LABELS

    return {
        key: label
        for key, label in ENTITY_LABELS.items()
        if key not in ADMIN_ONLY_ENTITIES
    }


def _audit_filters():
    entity = request.args.get("entity") or None

    if entity not in _audit_entity_labels():
        entity = None

    return {
        "entity_type": entity,
        "username": request.args.get("user") or None,
        "date_from": _parse_iso_date(request.args.get("date_from")),
        "date_to": _parse_iso_date(request.args.get("date_to")),
    }


@bp.route("/audit")
def audit_page():
    scope = _scope()
    filters = _audit_filters()

    page = max(1, request.args.get("page", 1, type=int))

    entries, total = get_audit_entries(
        scope,
        limit=_AUDIT_PAGE_SIZE,
        offset=(page - 1) * _AUDIT_PAGE_SIZE,
        **filters,
    )

    pages = max(1, -(-total // _AUDIT_PAGE_SIZE))

    query_values = {
        "entity": filters["entity_type"],
        "user": filters["username"],
        "date_from": request.args.get("date_from") if filters["date_from"] else None,
        "date_to": request.args.get("date_to") if filters["date_to"] else None,
        "branch_id": _selected_branch_id(),
    }

    return render_template(
        "audit.html",
        entries=entries,
        total=total,
        page=page,
        pages=pages,
        entity_labels=_audit_entity_labels(),
        all_entity_labels=ENTITY_LABELS,
        usernames=get_audit_usernames(accessible_branch_ids(current_user)),
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
        values=query_values,
        query_string=urlencode(
            {key: value for key, value in query_values.items() if value}
        ),
    )


@bp.route("/audit/export.csv")
def audit_export_csv():
    entries, _ = get_audit_entries(
        _scope(), limit=_AUDIT_EXPORT_LIMIT, **_audit_filters()
    )

    return Response(
        entries_to_csv(entries),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=zaznamy_zmien_{date.today().isoformat()}.csv"
            )
        },
    )


# ---------------------------------------------------------------------
# Absencie (dovolenka, PN, OČR, náhradné voľno)
# ---------------------------------------------------------------------

@bp.route("/absences")
def absences_page():
    employee_id = request.args.get("employee_id", type=int)
    scope = _scope()

    if employee_id:
        if _employee_in_access(employee_id):
            absences = get_absences_by_employee(employee_id)
        else:
            absences = []
    else:
        absences = get_absences(scope)

    employees = get_employees(scope)

    return render_template(
        "absences.html",
        absences=absences,
        employees=employees,
        selected_employee_id=employee_id,
        branches=_accessible_branches(),
        selected_branch_id=_selected_branch_id(),
    )


@bp.route("/absences/add", methods=["GET", "POST"])
def add_absence_page():
    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices(access)

    if request.method == "GET":
        preselected_employee_id = request.args.get("employee_id", type=int)

        if preselected_employee_id:
            form.employee_id.data = preselected_employee_id

        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    if form.end_date.data < form.start_date.data:
        flash("Dátum konca nemôže byť skorší ako dátum začiatku.", "error")

        return render_template(
            "add_absence.html",
            employees=employees,
            form=form,
        )

    employee_id = form.employee_id.data
    start_date = form.start_date.data.isoformat()
    end_date = form.end_date.data.isoformat()

    add_absence(
        employee_id,
        form.absence_type.data,
        start_date,
        end_date,
        note=form.note.data,
    )

    conflicting_shifts = [
        shift
        for shift in get_shifts_by_employee(employee_id)
        if start_date <= shift[3] <= end_date
    ]

    if conflicting_shifts:
        flash(
            f"Pozor: zamestnanec má v tomto období už naplánovaných "
            f"{len(conflicting_shifts)} smien - skontroluj ich na "
            "stránke /shifts.",
            "error",
        )

    return redirect("/absences")


@bp.route("/absences/edit/<int:absence_id>", methods=["GET", "POST"])
def edit_absence_page(absence_id):
    absence = get_absence(absence_id)

    if absence is None or not _employee_in_access(absence[1]):
        return "Neprítomnosť neexistuje.", 404

    access = accessible_branch_ids(current_user)

    employees = get_employees(access)

    form = AbsenceForm()
    form.employee_id.choices = _active_employee_choices(access)

    if request.method == "GET":
        form.employee_id.data = absence[1]
        form.absence_type.data = absence[2]
        form.start_date.data = date.fromisoformat(absence[3])
        form.end_date.data = date.fromisoformat(absence[4])
        form.note.data = absence[5]

        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    if not form.validate_on_submit():
        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    if form.end_date.data < form.start_date.data:
        flash("Dátum konca nemôže byť skorší ako dátum začiatku.", "error")

        return render_template(
            "edit_absence.html",
            absence=absence,
            employees=employees,
            form=form,
        )

    update_absence(
        absence_id,
        form.absence_type.data,
        form.start_date.data.isoformat(),
        form.end_date.data.isoformat(),
        note=form.note.data,
    )

    return redirect("/absences")


@bp.route("/absences/delete/<int:absence_id>", methods=["POST"])
def delete_absence_page(absence_id):
    if not _branch_in_access(absence_branch_id(absence_id)):
        return "Neprítomnosť neexistuje.", 404

    delete_absence(absence_id)

    return redirect("/absences")


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


def create_app(database_path=None):
    """Vytvorí a nakonfiguruje inštanciu Flask appky.

    ``database_path`` - explicitná cesta k SQLite súboru. Ak sa
    nezadá, použije sa ``DATABASE_URL`` z prostredia (napr.
    PostgreSQL), inak predvolený SQLite súbor z
    ``app/data/database.py``.
    Testy si sem posielajú vlastnú dočasnú cestu, aby mal každý test
    úplne izolovanú databázu (nutné pre správne fungovanie
    SQLAlchemy - engine sa viaže na appku pri jej vytvorení, nie pri
    každom volaní ako predtým s ručnými sqlite3 pripojeniami).
    """

    app = Flask(__name__)

    app.secret_key = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)

    if app.secret_key == _DEFAULT_SECRET_KEY:
        print(
            "UPOZORNENIE: beží sa s predvoleným (nebezpečným) SECRET_KEY. "
            "Pred nasadením do produkcie nastav premennú prostredia "
            "SECRET_KEY na náhodný, tajný reťazec - napr.:\n"
            "  python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    database_uri = resolve_database_uri(database_path)

    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    if not is_sqlite_uri(database_uri):
        # Server databázy môže zatvoriť nečinné spojenia - pred
        # použitím sa pripojenie overí, aby appka po výpadku/reštarte
        # databázy nepadala na "stale connection".
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    # Bezpečnejšie session cookie. Za HTTPS (pri nasadení pre
    # pobočky) nastav SESSION_COOKIE_SECURE=1.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    )

    if os.environ.get("BEHIND_PROXY", "0") == "1":
        # Appka beží za reverzným proxy (Caddy/nginx), ktoré rieši
        # HTTPS. Bez tohto by Flask nevidel, že spojenie je HTTPS
        # (adresy, cookies) ani skutočnú IP klienta.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1
        )

    migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"

    db.init_app(app)
    migrate.init_app(app, db, directory=str(migrations_dir))
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "main.login_page"
    login_manager.login_message = "Prosím, prihlás sa."
    login_manager.login_message_category = "error"

    app.extensions["login_throttle"] = LoginThrottle.from_env()

    init_audit()
    app.jinja_env.filters["local_time"] = to_local

    app.register_blueprint(bp)

    @app.after_request
    def _add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        if app.config["SESSION_COOKIE_SECURE"]:
            # Prehliadač si zapamätá, že appka je dostupná len cez HTTPS.
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
