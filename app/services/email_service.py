"""Odosielanie e-mailov: SMTP, fronta (outbox) a odosielacie vlákno.

Nastavenie cez premenné prostredia (``.env``):
  SMTP_HOST      server (bez neho sú e-mailové upozornenia vypnuté),
  SMTP_PORT      predvolene 587,
  SMTP_SECURITY  ``starttls`` (predvolené), ``ssl`` alebo ``none``,
  SMTP_USER, SMTP_PASSWORD   prihlásenie (nepovinné),
  SMTP_FROM      adresa odosielateľa (predvolene SMTP_USER),
  APP_BASE_URL   adresa appky pre odkazy v e-mailoch (nepovinné).

Udalosť e-mail len uloží do fronty (``enqueue_email``). Posiela ho
odosielač (``process_outbox``) - vlákno ``start_email_worker`` pri
spustenom serveri alebo príkaz ``manage.py send-emails``. Neúspešné
e-maily sa skúšajú znova (až ``MAX_ATTEMPTS`` krát).
"""

import logging
import os
import re
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import select, update

from app.extensions import db
from app.orm_models import EmailOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_STALE_SENDING_MINUTES = 10
_SMTP_TIMEOUT_SECONDS = 15

# Jednoduchá kontrola: bez medzier a zalomení riadkov (ochrana pred
# vkladaním hlavičiek), jedna zavináč, bodka v doméne.
_EMAIL_PATTERN = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")


def _now(moment=None):
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def normalize_email(value):
    """Vráti ``(adresa, None)`` (malými písmenami, bez okolitých medzier;
    prázdny vstup = ``None``) alebo ``(None, chybová správa)``."""

    value = (value or "").strip()

    if not value:
        return None, None

    if len(value) > 254 or not _EMAIL_PATTERN.match(value):
        return None, "Zadaj platnú e-mailovú adresu."

    return value.lower(), None


# ---------------------------------------------------------------------
# Nastavenie
# ---------------------------------------------------------------------

def smtp_configured():
    return bool(os.environ.get("SMTP_HOST", "").strip())


def base_url():
    return os.environ.get("APP_BASE_URL", "").strip().rstrip("/")


def sender_address():
    return (
        os.environ.get("SMTP_FROM", "").strip()
        or os.environ.get("SMTP_USER", "").strip()
        or "shift-planner@localhost"
    )


def _smtp_settings():
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()

    if security not in ("starttls", "ssl", "none"):
        security = "starttls"

    default_port = 465 if security == "ssl" else 587

    try:
        port = int(os.environ.get("SMTP_PORT", "").strip() or default_port)
    except ValueError:
        port = default_port

    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": port,
        "security": security,
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
    }


# ---------------------------------------------------------------------
# Odoslanie jedného e-mailu
# ---------------------------------------------------------------------

def build_message(recipient, subject, body):
    message = EmailMessage()
    message["From"] = sender_address()
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    # quoted-printable: čisto ASCII na drôte, prejde aj cez SMTP servery
    # bez podpory 8BITMIME (diakritika v tele ostáva zachovaná).
    message.set_content(body, charset="utf-8", cte="quoted-printable")

    return message


def send_message(recipient, subject, body):
    """Odošle e-mail cez SMTP hneď. Pri chybe vyhodí výnimku."""

    settings = _smtp_settings()

    if not settings["host"]:
        raise RuntimeError("SMTP_HOST nie je nastavený.")

    message = build_message(recipient, subject, body)

    if settings["security"] == "ssl":
        server = smtplib.SMTP_SSL(
            settings["host"], settings["port"], timeout=_SMTP_TIMEOUT_SECONDS
        )
    else:
        server = smtplib.SMTP(
            settings["host"], settings["port"], timeout=_SMTP_TIMEOUT_SECONDS
        )

    try:
        if settings["security"] == "starttls":
            server.starttls()

        if settings["user"]:
            server.login(settings["user"], settings["password"])

        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Fronta
# ---------------------------------------------------------------------

def enqueue_email(recipient, subject, body, commit=True):
    """Uloží e-mail do fronty (ak sú e-maily zapnuté). Vráti ID alebo
    ``None``. Nikdy nevyhodí chybu - upozornenie nesmie zhodiť operáciu."""

    if not smtp_configured():
        return None

    address, error = normalize_email(recipient)

    if error or address is None:
        return None

    try:
        row = EmailOutbox(
            recipient=address,
            subject=" ".join(subject.split())[:200],
            body=body,
            status="pending",
            attempts=0,
            created_at=_now(),
        )
        db.session.add(row)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return row.id
    except Exception:
        db.session.rollback()
        logger.exception("E-mail sa nepodarilo uložiť do fronty.")

        return None


def process_outbox(limit=50, now=None):
    """Odošle čakajúce e-maily. Vráti ``{"sent": n, "retry": n,
    "failed": n}``. Bezpečné spúšťať súbežne z viacerých procesov -
    e-mail sa pred odoslaním "zarezervuje"."""

    result = {"sent": 0, "retry": 0, "failed": 0}

    if not smtp_configured():
        return result

    now = now or datetime.now(timezone.utc)
    now_text = _now(now)

    # Zaseknuté (proces spadol počas odosielania) vrátime späť do frontu.
    stale_before = _now(now - timedelta(minutes=_STALE_SENDING_MINUTES))
    db.session.execute(
        update(EmailOutbox)
        .where(
            EmailOutbox.status == "sending",
            EmailOutbox.updated_at < stale_before,
        )
        .values(status="pending")
    )
    db.session.commit()

    ids = db.session.execute(
        select(EmailOutbox.id)
        .where(
            EmailOutbox.status == "pending",
            (EmailOutbox.next_attempt_at.is_(None))
            | (EmailOutbox.next_attempt_at <= now_text),
        )
        .order_by(EmailOutbox.id)
        .limit(limit)
    ).scalars().all()

    for email_id in ids:
        claimed = db.session.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id == email_id, EmailOutbox.status == "pending")
            .values(status="sending", updated_at=now_text)
        )
        db.session.commit()

        if claimed.rowcount != 1:
            continue

        row = db.session.get(EmailOutbox, email_id)

        try:
            send_message(row.recipient, row.subject, row.body)
        except Exception as error:
            row.attempts += 1
            row.last_error = str(error)[:250]
            row.updated_at = now_text

            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                result["failed"] += 1
            else:
                row.status = "pending"
                row.next_attempt_at = _now(
                    now + timedelta(minutes=5 * row.attempts)
                )
                result["retry"] += 1

            logger.warning(
                "E-mail #%s sa nepodarilo odoslať (pokus %s): %s",
                email_id, row.attempts, error,
            )
        else:
            row.status = "sent"
            row.sent_at = now_text
            row.updated_at = now_text
            row.last_error = None
            result["sent"] += 1

        db.session.commit()

    return result


def outbox_counts():
    """Počty e-mailov vo fronte podľa stavu."""

    from sqlalchemy import func

    rows = db.session.execute(
        select(EmailOutbox.status, func.count(EmailOutbox.id)).group_by(
            EmailOutbox.status
        )
    ).all()

    return {status: count for status, count in rows}


def prune_sent(days):
    """Zmaže odoslané e-maily staršie ako ``days`` dní. Vráti počet."""

    from sqlalchemy import delete

    cutoff = _now(datetime.now(timezone.utc) - timedelta(days=days))
    result = db.session.execute(
        delete(EmailOutbox).where(
            EmailOutbox.status == "sent", EmailOutbox.sent_at < cutoff
        )
    )
    db.session.commit()

    return result.rowcount


# ---------------------------------------------------------------------
# Odosielacie vlákno
# ---------------------------------------------------------------------

def start_email_worker(app, interval_seconds=30):
    """Spustí na pozadí vlákno, ktoré pravidelne odosiela e-maily z
    fronty. Vráti ``(vlákno, stop_udalosť)`` alebo ``None``, ak SMTP nie
    je nastavený."""

    if not smtp_configured():
        return None

    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                with app.app_context():
                    process_outbox()
            except Exception:
                logger.exception("Odosielač e-mailov narazil na chybu.")
            finally:
                try:
                    with app.app_context():
                        db.session.remove()
                except Exception:
                    pass

            stop.wait(interval_seconds)

    thread = threading.Thread(target=run, name="email-worker", daemon=True)
    thread.start()

    return thread, stop
