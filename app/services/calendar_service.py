"""Služby pre zostavenie kalendárneho pohľadu.

Vyčlenené z ``app/web/app.py``, aby webová vrstva ostala len tenkou
vrstvou spracovania HTTP požiadaviek.
"""

from calendar import monthrange
from datetime import date, timedelta


SHIFT_TYPE_CSS_CLASS = {
    "Ranná": "shift-morning",
    "Medzismena": "shift-mid",
    "Poobedná": "shift-afternoon",
    "Nočná": "shift-night",
}


def month_bounds(year, month):
    """Vráti (prvý_deň, posledný_deň) mesiaca ako ISO reťazce."""

    _, days_in_month = monthrange(year, month)

    first_day = date(year, month, 1).isoformat()
    last_day = date(year, month, days_in_month).isoformat()

    return first_day, last_day


def build_calendar_days(year, month):
    """Vráti zoznam dní na vykreslenie mriežky (None pre prázdne bunky)."""

    first_weekday, days_in_month = monthrange(year, month)

    calendar_days = [None] * first_weekday
    calendar_days.extend(range(1, days_in_month + 1))

    return calendar_days


def group_shifts_by_date(shifts):
    """Zoskupí smeny podľa dátumu (shift[3]) pre rýchle vykreslenie."""

    shifts_by_date = {}

    for shift in shifts:
        shifts_by_date.setdefault(shift[3], []).append(shift)

    return shifts_by_date


def shift_css_class(shift_type):
    return SHIFT_TYPE_CSS_CLASS.get(shift_type, "shift-default")


def shifts_to_fullcalendar_events(shifts):
    """Prevedie riadky smien na udalosti pre FullCalendar (JS knižnicu)."""

    events = []

    for shift in shifts:
        (
            shift_id,
            first_name,
            last_name,
            shift_date,
            start_time,
            end_time,
            shift_type,
            employee_id,
            department_id,
            department_name,
        ) = shift

        # Nočná smena (koniec <= začiatok) v skutočnosti končí až
        # nasledujúci deň - inak by ju FullCalendar vykreslil so
        # záporným/nulovým trvaním.
        end_date = shift_date

        if end_time <= start_time:
            end_date = (
                date.fromisoformat(shift_date) + timedelta(days=1)
            ).isoformat()

        events.append(
            {
                "id": shift_id,
                "title": f"{first_name} {last_name} ({shift_type})",
                "start": f"{shift_date}T{start_time}",
                "end": f"{end_date}T{end_time}",
                "extendedProps": {
                    "employeeId": employee_id,
                    "employeeName": f"{first_name} {last_name}",
                    "shiftType": shift_type,
                    "departmentId": department_id,
                    "departmentName": department_name,
                    "overnight": end_time <= start_time,
                },
                "className": shift_css_class(shift_type),
            }
        )

    return events
