"""Routy webovej vrstvy rozdelené podľa oblastí.

Všetky moduly pridávajú routy do jedného blueprintu ``main``
(``app.web.core.bp``), takže názvy endpointov (``main.<funkcia>``) sa
rozdelením súboru nezmenili. Import tohto balíčka routy zaregistruje.
"""

from app.web.routes import (  # noqa: F401
    absences,
    admin,
    audit,
    auth,
    availability,
    calendar_views,
    departments,
    employees,
    home,
    leave_requests,
    my_schedule,
    planning,
    reports,
    shifts,
    swaps,
)
