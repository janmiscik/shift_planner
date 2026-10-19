"""Úvodná stránka a stiahnutie zálohy databázy."""

from flask import (
    render_template,
    send_file,
)
from app.data.database import (
    is_sqlite_uri,
    sqlite_path_from_uri,
)
from app.services.audit_service import (
    record_event,
)
from app.services.employee_service import (
    get_employees,
)
from app.services.shift_service import (
    get_shifts,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _scope,
    _selected_branch_id,
)


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
