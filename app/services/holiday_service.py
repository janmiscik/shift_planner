"""Štátne sviatky a pracovné dni (Slovensko).

Pevné sviatky + pohyblivé (Veľký piatok a Veľkonočný pondelok podľa
dátumu Veľkej noci). Zoznam zodpovedá zákonu o štátnych sviatkoch, dňoch
pracovného pokoja a pamätných dňoch (1. 1., 6. 1., Veľký piatok,
Veľkonočný pondelok, 1. 5., 8. 5., 5. 7., 29. 8., 1. 9., 15. 9., 1. 11.,
17. 11., 24. 12., 25. 12., 26. 12.).
"""

from datetime import date, timedelta

# (mesiac, deň, názov)
_FIXED_HOLIDAYS = [
    (1, 1, "Deň vzniku Slovenskej republiky"),
    (1, 6, "Zjavenie Pána (Traja králi)"),
    (5, 1, "Sviatok práce"),
    (5, 8, "Deň víťazstva nad fašizmom"),
    (7, 5, "Sviatok svätého Cyrila a svätého Metoda"),
    (8, 29, "Výročie Slovenského národného povstania"),
    (9, 1, "Deň Ústavy Slovenskej republiky"),
    (9, 15, "Sedembolestná Panna Mária"),
    (11, 1, "Sviatok všetkých svätých"),
    (11, 17, "Deň boja za slobodu a demokraciu"),
    (12, 24, "Štedrý deň"),
    (12, 25, "Prvý sviatok vianočný"),
    (12, 26, "Druhý sviatok vianočný"),
]

_cache = {}


def easter_sunday(year):
    """Dátum Veľkonočnej nedele (gregoriánsky kalendár, algoritmus
    Meeus/Jones/Butcher)."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month, day = divmod(h + weekday_offset - 7 * m + 114, 31)

    return date(year, month, day + 1)


def slovak_public_holidays(year):
    """Slovník ``{date: názov}`` štátnych sviatkov v danom roku."""

    if year not in _cache:
        holidays = {
            date(year, month, day): name for month, day, name in _FIXED_HOLIDAYS
        }
        easter = easter_sunday(year)
        holidays[easter - timedelta(days=2)] = "Veľký piatok"
        holidays[easter + timedelta(days=1)] = "Veľkonočný pondelok"

        _cache[year] = holidays

    return _cache[year]


def is_public_holiday(day):
    return day in slovak_public_holidays(day.year)


def is_working_day(day):
    """Pracovný deň = pondelok až piatok, ktorý nie je štátnym sviatkom."""

    return day.weekday() < 5 and not is_public_holiday(day)


def working_days_between(start, end):
    """Počet pracovných dní v období od-do (vrátane oboch krajov)."""

    if end < start:
        return 0

    return sum(
        1
        for offset in range((end - start).days + 1)
        if is_working_day(start + timedelta(days=offset))
    )
