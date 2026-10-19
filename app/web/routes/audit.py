"""Záznamy zmien (audit log)."""

from datetime import date
from urllib.parse import urlencode
from flask import (
    Response,
    render_template,
    request,
)
from flask_login import (
    current_user,
)
from app.orm_models import ROLE_ADMIN
from app.services.audit_service import (
    ADMIN_ONLY_ENTITIES,
    ENTITY_LABELS,
    entries_to_csv,
    get_audit_entries,
    get_audit_usernames,
)
from app.services.access_service import (
    accessible_branch_ids,
)

from app.web.core import bp
from app.web.helpers import (
    _accessible_branches,
    _scope,
    _selected_branch_id,
)


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
