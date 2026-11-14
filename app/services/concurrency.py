"""Ochrana pred súbežnými zápismi smien (dvaja manažéri naraz).

Kontrola pravidiel (``validate_shift``: prekrytie, odpočinok, týždenný
fond, absencia, oddelenie) a samotný zápis (``add_shift``/
``update_shift``/``move_shift``) sú DVA kroky. Bez ochrany môžu dve
súbežné požiadavky obe prejsť kontrolou (vidia ešte starý, bezkolízny
stav) a obe zapísať - výsledkom sú dve prekrývajúce sa smeny, hoci
appka to má zakázané.

``lock_employee_for_write(employee_id)`` je kontextový manažér, ktorý
zaistí, že pre JEDNÉHO zamestnanca prebehne celý blok "over pravidlá +
zapíš" vždy len pre jednu požiadavku naraz - druhá počká, kým prvá
transakciu dokončí (commit alebo rollback), a až potom uvidí jej
výsledok.

Spôsob zámku závisí od databázy:
  - **PostgreSQL**: transakčný advisory lock
    (``pg_advisory_xact_lock``) na ID zamestnanca. Uvoľní sa sám pri
    commite/rollbacku danej DB transakcie; funguje naprieč všetkými
    procesmi/vláknami pripojenými k tej istej databáze.
  - **SQLite**: SQLite dovoľuje zapisovať len jednému pripojeniu
    naraz. Bežná (DEFERRED) transakcia získa zápisový zámok až pri
    PRVOM zápise - čo by bolo neskoro (po kontrole pravidiel). Preto sa
    na začiatku bloku urobí neškodný zápis (``UPDATE employees SET
    id = id``), ktorý zápisový zámok vynúti HNEĎ, pred kontrolou
    pravidiel. Druhé vlákno/proces, ktoré sa o to isté pokúsi súčasne,
    počká (``PRAGMA busy_timeout`` - nastaviteľný cez
    ``SQLITE_BUSY_TIMEOUT``, pozri ``app/web/app.py``), kým prvé
    transakciu nedokončí - funguje to aj naprieč procesmi, pokiaľ
    zdieľajú ten istý súbor databázy.

Pri inom dialekte (žiadny bežne používaný) sa zámok preskočí - appka
funguje, len bez tejto ochrany.
"""

import contextlib
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db

# Max. čas čakania na zámok inej súbežnej požiadavky, kým appka vzdá a
# ukáže používateľovi zrozumiteľnú chybu namiesto toho, aby visela
# donekonečna (napr. pri zaseknutej inej transakcii).
LOCK_TIMEOUT_SECONDS = 20


class ShiftLockTimeout(RuntimeError):
    """Zámok na zamestnanca sa nepodarilo získať včas - pravdepodobne
    iná požiadavka práve upravuje jeho smeny."""


def _dialect_name():
    return db.session.get_bind().dialect.name


def _lock_postgres(employee_id):
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(employee_id)}
    )


def _lock_sqlite(employee_id):
    """Vynúti zápisový zámok (eskaláciu z DEFERRED na RESERVED) HNEĎ,
    namiesto toho, aby k nej došlo až pri neskoršom zápise. Čaká najviac
    ``LOCK_TIMEOUT_SECONDS``."""

    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS

    while True:
        try:
            db.session.execute(
                text("UPDATE employees SET id = id WHERE id = :id"),
                {"id": int(employee_id)},
            )
            return
        except OperationalError as error:
            db.session.rollback()

            if "locked" not in str(error).lower() or time.monotonic() >= deadline:
                raise ShiftLockTimeout(
                    "Databáza je práve uzamknutá inou zmenou - skús to "
                    "znova o chvíľu."
                ) from error

            time.sleep(0.05)


@contextlib.contextmanager
def lock_employee_for_write(employee_id):
    """Zamkne zamestnanca na dobu trvania bloku (pozri modulový
    docstring). Používaj okolo ``validate_shift`` AJ zápisu (vrátane
    jeho ``db.session.commit()``) v tom istom bloku - zámok sa musí
    držať počas CELÉHO "over + zapíš" kroku."""

    dialect = _dialect_name()

    if dialect == "postgresql":
        _lock_postgres(employee_id)
    elif dialect == "sqlite":
        _lock_sqlite(employee_id)

    yield


@contextlib.contextmanager
def lock_employees_for_write(employee_ids):
    """Ako ``lock_employee_for_write``, ale pre VIACERÝCH zamestnancov
    naraz (napr. výmena smien medzi dvoma ľuďmi). Zamyká sa vždy v
    rovnakom (vzostupnom) poradí ID, aby dve súbežné požiadavky
    zamykajúce tú istú dvojicu nemohli skončiť v krížovom čakaní
    (deadlocku)."""

    ordered = sorted({int(employee_id) for employee_id in employee_ids})

    with contextlib.ExitStack() as stack:
        for employee_id in ordered:
            stack.enter_context(lock_employee_for_write(employee_id))

        yield
