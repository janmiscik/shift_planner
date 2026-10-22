"""Testy e-mailových upozornení: fronta, odosielanie, príjemcovia, nastavenia."""

import re
import smtplib
import time
from datetime import date, datetime, timedelta, timezone

import pytest


def _csrf(html):
    for tag in re.findall(r"<input[^>]*>", html):
        if 'name="csrf_token"' in tag:
            return re.search(r'value="([^"]+)"', tag).group(1)

    raise AssertionError("CSRF token sa nenašiel")


TODAY = date.today()
MON = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
WEEK = f"{MON.strftime('%d.%m.')}–{(MON + timedelta(days=6)).strftime('%d.%m.%Y')}"


class FakeSMTP:
    """Nahrádza smtplib.SMTP / SMTP_SSL - nič sa neposiela do siete."""

    instances = []
    sent = []
    failures_left = 0

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = False
        self.credentials = None
        self.ssl = False
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.credentials = (user, password)

    def send_message(self, message):
        if FakeSMTP.failures_left > 0:
            FakeSMTP.failures_left -= 1
            raise smtplib.SMTPException("server nedostupný")

        FakeSMTP.sent.append(message)

    def quit(self):
        pass


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host, port, timeout=None):
        super().__init__(host, port, timeout)
        self.ssl = True


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.sent, FakeSMTP.failures_left = [], [], 0

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)

    for name, value in {
        "SMTP_HOST": "smtp.test",
        "SMTP_PORT": "587",
        "SMTP_SECURITY": "starttls",
        "SMTP_USER": "planner@firma.sk",
        "SMTP_PASSWORD": "tajne",
        "SMTP_FROM": "Plánovač <planner@firma.sk>",
        "APP_BASE_URL": "https://plan.firma.sk/",
    }.items():
        monkeypatch.setenv(name, value)

    return FakeSMTP


@pytest.fixture
def ew(tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "mail.db")
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)

    ids = {}

    with app.app_context():
        db.create_all()

        from app.services.auth_service import (
            create_admin,
            create_employee_user,
            create_manager,
        )
        from app.services.branch_service import add_branch
        from app.services.employee_service import add_employee
        from app.services.shift_service import add_shift

        ids["a"] = add_branch("Košice")
        ids["b"] = add_branch("Prešov")

        for key, first, last, branch in (
            ("anna", "Anna", "Prvá", "a"), ("cyril", "Cyril", "Tretí", "a"),
            ("dana", "Dana", "Štvrtá", "a"), ("eva", "Eva", "Piata", "a"),
            ("fero", "Fero", "Šiesty", "a"), ("boris", "Boris", "Druhý", "b"),
        ):
            ids[key] = add_employee(first, last, "Op", 40, ids[branch])

        create_employee_user(ids["anna"], "emp_anna", "emppass12", email="anna@firma.sk")
        create_employee_user(ids["cyril"], "emp_cyril", "emppass12", email="cyril@firma.sk")
        create_employee_user(ids["dana"], "emp_dana", "emppass12")                       # bez e-mailu
        create_employee_user(ids["eva"], "emp_eva", "emppass12", email="eva@firma.sk")   # vypnuté
        create_employee_user(ids["boris"], "emp_boris", "emppass12", email="boris@firma.sk")
        # Fero nemá účet.

        create_admin("admin", "adminpass1", email="admin@firma.sk")
        create_manager("mgr_a", "managerpass1", [ids["a"]], email="mgr@firma.sk")
        create_manager("mgr_x", "managerpass1", [ids["a"]])                              # bez e-mailu
        create_manager("mgr_b", "managerpass1", [ids["b"]], email="mgrb@firma.sk")

        from app.services.auth_service import get_user_by_username, set_user_email

        set_user_email(get_user_by_username("emp_eva").id, "eva@firma.sk", notify_email=False)

        def shift(emp, offset, start="06:00", end="14:00", published=False):
            return add_shift(
                emp, (MON + timedelta(days=offset)).isoformat(), start, end,
                "Ranná", published=published,
            )

        ids["s_anna1"] = shift(ids["anna"], 0)
        ids["s_anna2"] = shift(ids["anna"], 2, "14:00", "22:00")
        ids["s_cyril"] = shift(ids["cyril"], 1)
        ids["s_dana"] = shift(ids["dana"], 1)
        ids["s_eva"] = shift(ids["eva"], 1)
        ids["s_fero"] = shift(ids["fero"], 1)
        ids["s_boris"] = shift(ids["boris"], 0)

    app.test_ids = ids

    return app


def login(app, username, password):
    client = app.test_client()
    token = _csrf(client.get("/login").get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": username, "password": password})
    return client


def post(client, page, url, data=None, follow=False):
    token = _csrf(client.get(page).get_data(as_text=True))

    return client.post(url, data={"csrf_token": token, **(data or {})}, follow_redirects=follow)


def outbox(app, status=None):
    from app.extensions import db
    from app.orm_models import EmailOutbox

    with app.app_context():
        query = db.session.query(EmailOutbox).order_by(EmailOutbox.id)

        if status:
            query = query.filter(EmailOutbox.status == status)

        return [
            {"id": r.id, "to": r.recipient, "subject": r.subject, "body": r.body,
             "status": r.status, "attempts": r.attempts, "error": r.last_error,
             "next": r.next_attempt_at}
            for r in query.all()
        ]


def recipients(app):
    return sorted(row["to"] for row in outbox(app))


def process(app, **kwargs):
    from app.services.email_service import process_outbox

    with app.app_context():
        return process_outbox(**kwargs)


def publish(app, branch="a"):
    from app.services.publish_service import publish_week

    with app.app_context():
        return publish_week(MON, app.test_ids[branch])


# ---------------------------------------------------------------------
# Adresy a zapnutie
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "given, expected",
    [
        (" Jan@Firma.SK ", ("jan@firma.sk", None)),
        ("", (None, None)),
        (None, (None, None)),
        ("   ", (None, None)),
        ("bez-zavinaca", (None, "Zadaj platnú e-mailovú adresu.")),
        ("a@b", (None, "Zadaj platnú e-mailovú adresu.")),
        ("a b@c.sk", (None, "Zadaj platnú e-mailovú adresu.")),
        ("a@b.sk\nBcc: x@y.sk", (None, "Zadaj platnú e-mailovú adresu.")),
        ("a@b.sk,c@d.sk", (None, "Zadaj platnú e-mailovú adresu.")),
        ("<a@b.sk>", (None, "Zadaj platnú e-mailovú adresu.")),
        ("a" * 250 + "@b.sk", (None, "Zadaj platnú e-mailovú adresu.")),
    ],
)
def test_normalize_email(given, expected):
    from app.services.email_service import normalize_email

    assert normalize_email(given) == expected


def test_nothing_is_queued_when_smtp_is_not_configured(ew, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")

    publish(ew)

    assert outbox(ew) == []

    from app.services.email_service import enqueue_email, process_outbox, start_email_worker

    with ew.app_context():
        assert enqueue_email("a@b.sk", "x", "y") is None
        assert process_outbox() == {"sent": 0, "retry": 0, "failed": 0}
        assert start_email_worker(ew) is None


def test_enqueue_sanitizes_and_validates(ew):
    from app.services.email_service import enqueue_email

    with ew.app_context():
        assert enqueue_email("zle", "x", "y") is None
        assert enqueue_email("ok@firma.sk", "Predmet\r\nBcc: x@y.sk", "telo") is not None

    row = outbox(ew)[0]
    assert "\n" not in row["subject"] and "\r" not in row["subject"]
    assert row["to"] == "ok@firma.sk"


# ---------------------------------------------------------------------
# Zverejnený rozpis
# ---------------------------------------------------------------------

def test_publishing_notifies_only_employees_who_can_receive(ew):
    publish(ew)

    # Anna, Cyril. Nie Dana (bez adresy), Eva (vypnuté), Fero (bez účtu),
    # Boris (iná pobočka, nezverejnené).
    assert recipients(ew) == ["anna@firma.sk", "cyril@firma.sk"]


def test_schedule_email_content(ew):
    publish(ew)

    mail = [r for r in outbox(ew) if r["to"] == "anna@firma.sk"][0]

    assert mail["subject"] == f"Nový rozpis smien: {WEEK} (Košice)"
    assert "Dobrý deň, Anna," in mail["body"]
    assert f"Po {MON.strftime('%d.%m.%Y')}  06:00–14:00 (Ranná)" in mail["body"]
    assert "St " in mail["body"] and "14:00–22:00" in mail["body"]
    assert "https://plan.firma.sk/my-schedule" in mail["body"]          # bez dvojitého lomítka
    assert "https://plan.firma.sk/account/settings" in mail["body"]

    cyril = [r for r in outbox(ew) if r["to"] == "cyril@firma.sk"][0]
    assert "Anna" not in cyril["body"].split("Dobrý deň")[1].split("\n", 1)[1]   # cudzie smeny nie
    assert "14:00–22:00" not in cyril["body"]


def test_republishing_without_new_shifts_sends_nothing(ew):
    publish(ew)
    before = len(outbox(ew))

    publish(ew)

    assert len(outbox(ew)) == before


def test_unpublishing_sends_nothing(ew):
    from app.services.publish_service import unpublish_week

    publish(ew)
    before = len(outbox(ew))

    with ew.app_context():
        unpublish_week(MON, ew.test_ids["a"])

    assert len(outbox(ew)) == before


def test_links_are_omitted_without_base_url(ew, monkeypatch):
    monkeypatch.delenv("APP_BASE_URL")

    publish(ew)

    body = outbox(ew)[0]["body"]
    assert "http" not in body and "Môj účet" in body


# ---------------------------------------------------------------------
# Odosielanie
# ---------------------------------------------------------------------

def test_process_outbox_sends_through_smtp(ew):
    publish(ew)

    result = process(ew)

    assert result == {"sent": 2, "retry": 0, "failed": 0}
    assert [r["status"] for r in outbox(ew)] == ["sent", "sent"]

    assert len(FakeSMTP.sent) == 2
    message = FakeSMTP.sent[0]
    assert message["To"] == "anna@firma.sk" and "Plánovač" in message["From"]
    assert message["Subject"].startswith("Nový rozpis smien")
    assert "Dobrý deň, Anna," in message.get_content()
    assert message["Message-ID"] and message["Date"]

    connection = FakeSMTP.instances[0]
    assert (connection.host, connection.port) == ("smtp.test", 587)
    assert connection.tls and connection.credentials == ("planner@firma.sk", "tajne")

    # Druhé spustenie už nemá čo posielať.
    assert process(ew)["sent"] == 0 and len(FakeSMTP.sent) == 2


def test_ssl_and_plain_security_modes(ew, monkeypatch):
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    monkeypatch.delenv("SMTP_PORT")
    publish(ew)
    process(ew)

    connection = FakeSMTP.instances[0]
    assert connection.ssl and connection.port == 465 and not connection.tls

    FakeSMTP.instances.clear()
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_USER", "")

    from app.services.email_service import enqueue_email

    with ew.app_context():
        enqueue_email("x@firma.sk", "t", "b")

    process(ew)

    connection = FakeSMTP.instances[0]
    assert not connection.ssl and not connection.tls and connection.credentials is None


def test_failed_send_is_retried_with_backoff_and_eventually_gives_up(ew):
    from app.services.email_service import MAX_ATTEMPTS

    publish(ew)
    FakeSMTP.failures_left = 999

    now = datetime.now(timezone.utc)

    result = process(ew, now=now)
    assert result == {"sent": 0, "retry": 2, "failed": 0}

    row = outbox(ew)[0]
    assert row["status"] == "pending" and row["attempts"] == 1
    assert "server nedostupný" in row["error"] and row["next"] > now.strftime("%Y-%m-%d %H:%M:%S")

    # Pred uplynutím čakania sa nič neskúša.
    assert process(ew, now=now + timedelta(minutes=1)) == {"sent": 0, "retry": 0, "failed": 0}

    for attempt in range(2, MAX_ATTEMPTS + 1):
        now += timedelta(hours=1)
        result = process(ew, now=now)

    assert result["failed"] == 2
    assert {r["status"] for r in outbox(ew)} == {"failed"}
    assert outbox(ew)[0]["attempts"] == MAX_ATTEMPTS

    # Zlyhané sa už neposielajú.
    FakeSMTP.failures_left = 0
    assert process(ew, now=now + timedelta(days=1))["sent"] == 0


def test_temporary_failure_then_success(ew):
    publish(ew)
    FakeSMTP.failures_left = 1

    now = datetime.now(timezone.utc)
    assert process(ew, now=now) == {"sent": 1, "retry": 1, "failed": 0}

    assert process(ew, now=now + timedelta(hours=1))["sent"] == 1
    assert {r["status"] for r in outbox(ew)} == {"sent"}


def test_stale_sending_rows_are_recovered_but_fresh_ones_are_left(ew):
    from app.extensions import db
    from app.orm_models import EmailOutbox
    from app.services.email_service import enqueue_email

    now = datetime.now(timezone.utc)

    with ew.app_context():
        stale = enqueue_email("stary@firma.sk", "stary", "b")
        fresh = enqueue_email("novy@firma.sk", "novy", "b")

        db.session.get(EmailOutbox, stale).status = "sending"
        db.session.get(EmailOutbox, stale).updated_at = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        db.session.get(EmailOutbox, fresh).status = "sending"
        db.session.get(EmailOutbox, fresh).updated_at = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.session.commit()

    process(ew, now=now)

    assert [m["To"] for m in FakeSMTP.sent] == ["stary@firma.sk"]
    assert {r["to"]: r["status"] for r in outbox(ew)} == {
        "stary@firma.sk": "sent", "novy@firma.sk": "sending",
    }


def test_background_worker_sends_and_stops(ew):
    from app.services.email_service import start_email_worker

    publish(ew)

    thread, stop = start_email_worker(ew, interval_seconds=0.05)

    try:
        deadline = time.time() + 5

        while time.time() < deadline and len(FakeSMTP.sent) < 2:
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert len(FakeSMTP.sent) == 2
    assert not thread.is_alive()


def test_counts_and_prune(ew):
    from app.extensions import db
    from app.orm_models import EmailOutbox
    from app.services.email_service import outbox_counts, prune_sent

    publish(ew)
    process(ew)

    with ew.app_context():
        assert outbox_counts() == {"sent": 2}

        old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        row = db.session.query(EmailOutbox).first()
        row.sent_at = old
        db.session.commit()

        assert prune_sent(30) == 1
        assert outbox_counts() == {"sent": 1}


# ---------------------------------------------------------------------
# Žiadosti o voľno
# ---------------------------------------------------------------------

def test_leave_request_notifies_branch_managers_and_decision_notifies_employee(ew):
    from app.services.leave_request_service import approve_request, create_request, reject_request

    ids = ew.test_ids
    start = (TODAY + timedelta(days=30)).isoformat()
    end = (TODAY + timedelta(days=32)).isoformat()

    with ew.app_context():
        request_id, _ = create_request(ids["anna"], "Dovolenka", start, end, "Rodinná oslava")

    # Manažér pobočky s adresou; nie mgr_x (bez adresy), mgr_b (iná pobočka), admin.
    assert recipients(ew) == ["mgr@firma.sk"]
    mail = outbox(ew)[0]
    assert mail["subject"] == "Nová žiadosť o voľno: Anna Prvá"
    assert "Rodinná oslava" in mail["body"] and "dovolenka" in mail["body"]
    assert "https://plan.firma.sk/leave-requests" in mail["body"]

    with ew.app_context():
        approve_request(request_id, None, "Uži si to")

    decision = outbox(ew)[-1]
    assert decision["to"] == "anna@firma.sk"
    assert decision["subject"] == "Žiadosť o voľno schválená"
    assert "schválená" in decision["body"] and "Uži si to" in decision["body"]

    with ew.app_context():
        second, _ = create_request(ids["anna"], "Dovolenka", (TODAY + timedelta(days=60)).isoformat(), (TODAY + timedelta(days=61)).isoformat())
        reject_request(second, None, "Inventúra")

    assert outbox(ew)[-1]["subject"] == "Žiadosť o voľno zamietnutá"
    assert "Inventúra" in outbox(ew)[-1]["body"]


def test_admins_are_the_fallback_when_no_branch_manager_can_receive(ew):
    from app.extensions import db
    from app.orm_models import User
    from app.services.leave_request_service import create_request

    with ew.app_context():
        user = db.session.query(User).filter_by(username="mgr_a").one()
        user.notify_email = 0
        db.session.commit()

        create_request(ew.test_ids["anna"], "Dovolenka", (TODAY + timedelta(days=30)).isoformat(), (TODAY + timedelta(days=31)).isoformat())

    assert recipients(ew) == ["admin@firma.sk"]


def test_requester_who_is_also_a_manager_is_not_notified_about_own_request(ew):
    from app.services.auth_service import set_manager_function
    from app.services.leave_request_service import create_request

    ids = ew.test_ids

    with ew.app_context():
        set_manager_function(ids["anna"], True, [ids["a"]])
        create_request(ids["anna"], "Dovolenka", (TODAY + timedelta(days=30)).isoformat(), (TODAY + timedelta(days=31)).isoformat())

    assert "anna@firma.sk" not in recipients(ew)
    assert recipients(ew) == ["mgr@firma.sk"]


# ---------------------------------------------------------------------
# Výmeny smien
# ---------------------------------------------------------------------

def _swap_world(ew):
    from app.services.shift_service import add_shift

    ids = ew.test_ids

    with ew.app_context():
        ids["swap_shift"] = add_shift(
            ids["anna"], (MON + timedelta(days=4)).isoformat(), "06:00", "14:00", "Ranná", published=True
        )


def test_swap_flow_notifies_the_right_people(ew):
    from app.services.shift_swap_service import approve_swap, colleague_respond, create_swap, reject_swap

    _swap_world(ew)
    ids = ew.test_ids

    with ew.app_context():
        swap_id, error = create_swap(ids["anna"], ids["cyril"], ids["swap_shift"], note="Mám lekára")

    assert error is None
    assert recipients(ew) == ["cyril@firma.sk"]
    assert outbox(ew)[-1]["subject"] == "Anna Prvá ti navrhuje výmenu smeny"
    assert "Mám lekára" in outbox(ew)[-1]["body"]
    assert "https://plan.firma.sk/my-swaps" in outbox(ew)[-1]["body"]

    with ew.app_context():
        colleague_respond(swap_id, ids["cyril"], True)

    assert outbox(ew)[-1]["to"] == "mgr@firma.sk"
    assert outbox(ew)[-1]["subject"] == "Výmena smien čaká na schválenie"
    assert "/swap-requests" in outbox(ew)[-1]["body"]

    with ew.app_context():
        ok, errors = approve_swap(swap_id, None)

    assert ok, errors
    tail = outbox(ew)[-2:]
    assert sorted(m["to"] for m in tail) == ["anna@firma.sk", "cyril@firma.sk"]
    assert all(m["subject"] == "Výmena smien schválená" for m in tail)


def test_swap_decline_and_rejection_notifications(ew):
    from app.services.shift_swap_service import colleague_respond, create_swap, reject_swap

    _swap_world(ew)
    ids = ew.test_ids

    with ew.app_context():
        swap_id, _ = create_swap(ids["anna"], ids["cyril"], ids["swap_shift"])
        colleague_respond(swap_id, ids["cyril"], False)

    assert outbox(ew)[-1]["to"] == "anna@firma.sk"
    assert outbox(ew)[-1]["subject"] == "Kolega odmietol tvoj návrh výmeny"

    with ew.app_context():
        second, _ = create_swap(ids["anna"], ids["cyril"], ids["swap_shift"])
        colleague_respond(second, ids["cyril"], True)
        reject_swap(second, None, "Chýba obsadenie")

    tail = outbox(ew)[-2:]
    assert sorted(m["to"] for m in tail) == ["anna@firma.sk", "cyril@firma.sk"]
    assert all("Chýba obsadenie" in m["body"] for m in tail)


# ---------------------------------------------------------------------
# Odolnosť
# ---------------------------------------------------------------------

def test_a_failing_notification_never_breaks_the_operation(ew, monkeypatch):
    import app.services.notification_service as notifications

    def broken(*args, **kwargs):
        raise RuntimeError("chyba pri príprave správy")

    monkeypatch.setattr(notifications, "_send_to", broken)

    from app.services.leave_request_service import create_request

    with ew.app_context():
        request_id, error = create_request(
            ew.test_ids["anna"], "Dovolenka",
            (TODAY + timedelta(days=30)).isoformat(), (TODAY + timedelta(days=31)).isoformat(),
        )

    assert error is None and request_id
    assert publish(ew) == 6          # publikovanie prebehlo aj napriek chybe
    assert outbox(ew) == []


# ---------------------------------------------------------------------
# Nastavenia účtu
# ---------------------------------------------------------------------

def test_account_settings_for_every_role(ew):
    for user, password in (("emp_anna", "emppass12"), ("mgr_a", "managerpass1"), ("admin", "adminpass1")):
        client = login(ew, user, password)

        html = client.get("/account/settings").get_data(as_text=True)
        assert "Môj účet" in html
        assert 'href="/account/settings"' in client.get("/").get_data(as_text=True) or user == "emp_anna"


def test_user_can_set_email_and_switch_notifications_off(ew):
    from app.services.auth_service import get_user_by_username

    anna = login(ew, "emp_anna", "emppass12")

    html = anna.get("/account/settings").get_data(as_text=True)
    assert 'value="anna@firma.sk"' in html and "checked" in html

    post(anna, "/account/settings", "/account/settings", {"email": " Nova.Anna@Firma.SK ", "notify_email": "on"})

    with ew.app_context():
        user = get_user_by_username("emp_anna")
        assert (user.email, user.notify_email) == ("nova.anna@firma.sk", 1)

    post(anna, "/account/settings", "/account/settings", {"email": "nova.anna@firma.sk"})

    with ew.app_context():
        assert get_user_by_username("emp_anna").notify_email == 0

    publish(ew)
    assert "nova.anna@firma.sk" not in recipients(ew)

    # Prázdna adresa = bez e-mailu.
    post(anna, "/account/settings", "/account/settings", {"email": "", "notify_email": "on"})

    with ew.app_context():
        assert get_user_by_username("emp_anna").email is None


def test_invalid_email_is_rejected(ew):
    from app.services.auth_service import get_user_by_username

    anna = login(ew, "emp_anna", "emppass12")

    response = post(anna, "/account/settings", "/account/settings", {"email": "nie-je-email"}, follow=True)

    assert "platnú e-mailovú adresu" in response.get_data(as_text=True)

    with ew.app_context():
        assert get_user_by_username("emp_anna").email == "anna@firma.sk"


def test_test_email_button(ew):
    anna = login(ew, "emp_anna", "emppass12")

    response = post(anna, "/account/settings", "/account/settings/test-email", follow=True)

    assert "Skúšobný e-mail bol odoslaný na anna@firma.sk" in response.get_data(as_text=True)
    assert [m["To"] for m in FakeSMTP.sent] == ["anna@firma.sk"]

    # Chyba SMTP sa zobrazí, appka nespadne.
    FakeSMTP.failures_left = 1
    response = post(anna, "/account/settings", "/account/settings/test-email", follow=True)
    assert "Odoslanie zlyhalo" in response.get_data(as_text=True)

    # Bez uloženej adresy.
    dana = login(ew, "emp_dana", "emppass12")
    response = post(dana, "/account/settings", "/account/settings/test-email", follow=True)
    assert "Najprv ulož svoju e-mailovú adresu" in response.get_data(as_text=True)


def test_test_email_button_without_smtp(ew, monkeypatch):
    anna = login(ew, "emp_anna", "emppass12")
    monkeypatch.delenv("SMTP_HOST")

    response = post(anna, "/account/settings", "/account/settings/test-email", follow=True)

    assert "nie sú na serveri nastavené" in response.get_data(as_text=True)
    page = " ".join(anna.get("/account/settings").get_data(as_text=True).split())
    assert "nie sú na serveri zapnuté" in page
    assert FakeSMTP.sent == []


# ---------------------------------------------------------------------
# Správa adries (admin)
# ---------------------------------------------------------------------

def test_admin_sets_emails_for_staff_and_employee_accounts(ew):
    from app.services.auth_service import get_user_by_username

    admin = login(ew, "admin", "adminpass1")
    ids = ew.test_ids

    post(admin, "/users/add", "/users/add", {
        "username": "novy_mgr", "password": "novypass12", "role": "manager",
        "branch_ids": [str(ids["a"])], "email": "Novy@Firma.sk",
    })

    with ew.app_context():
        assert get_user_by_username("novy_mgr").email == "novy@firma.sk"
        user_id = get_user_by_username("novy_mgr").id
        dana_id = get_user_by_username("emp_dana").id

    # Úprava s adresou; úprava BEZ poľa e-mail adresu nevymaže.
    post(admin, f"/users/edit/{user_id}", f"/users/edit/{user_id}", {
        "username": "novy_mgr", "branch_ids": [str(ids["a"])], "email": "ine@firma.sk",
    })
    post(admin, f"/users/edit/{user_id}", f"/users/edit/{user_id}", {
        "username": "novy_mgr", "branch_ids": [str(ids["a"])],
    })

    with ew.app_context():
        assert get_user_by_username("novy_mgr").email == "ine@firma.sk"

    # Účet zamestnanca: úprava cez stránku zamestnanca.
    post(admin, f"/employees/edit/{ids['dana']}", f"/employees/{ids['dana']}/credentials/update", {
        "username": "emp_dana", "password": "", "email": "dana@firma.sk",
    })

    with ew.app_context():
        assert get_user_by_username("emp_dana").email == "dana@firma.sk"

    # Neplatná adresa sa nezapíše.
    response = post(admin, f"/employees/edit/{ids['dana']}", f"/employees/{ids['dana']}/credentials/update", {
        "username": "emp_dana", "password": "", "email": "zle",
    }, follow=True)
    assert "platnú e-mailovú adresu" in response.get_data(as_text=True)

    with ew.app_context():
        assert get_user_by_username("emp_dana").email == "dana@firma.sk"


def test_credentials_update_without_email_field_keeps_the_address(ew):
    from app.services.auth_service import get_user_by_username

    admin = login(ew, "admin", "adminpass1")
    ids = ew.test_ids

    post(admin, f"/employees/edit/{ids['anna']}", f"/employees/{ids['anna']}/credentials/update", {
        "username": "emp_anna", "password": "",
    })

    with ew.app_context():
        assert get_user_by_username("emp_anna").email == "anna@firma.sk"


def test_new_employee_and_credentials_can_have_an_email(ew):
    from app.services.auth_service import get_user_by_username

    admin = login(ew, "admin", "adminpass1")
    ids = ew.test_ids

    post(admin, "/employees/add", "/employees/add", {
        "branch_id": str(ids["a"]), "first_name": "Gita", "last_name": "Siedma",
        "position": "Op", "weekly_hours": "40", "username": "gita", "password": "gitapass1",
        "email": "gita@firma.sk",
    })

    with ew.app_context():
        assert get_user_by_username("gita").email == "gita@firma.sk"

    # Účet pre zamestnanca bez účtu (Fero).
    post(admin, f"/employees/edit/{ids['fero']}", f"/employees/{ids['fero']}/credentials/create", {
        "username": "fero", "password": "feropass1", "email": "fero@firma.sk",
    })

    with ew.app_context():
        assert get_user_by_username("fero").email == "fero@firma.sk"


def test_email_changes_are_audited(ew):
    from app.extensions import db
    from app.orm_models import AuditLog

    anna = login(ew, "emp_anna", "emppass12")
    post(anna, "/account/settings", "/account/settings", {"email": "nova@firma.sk"})

    with ew.app_context():
        summaries = [r.summary for r in db.session.query(AuditLog).filter(AuditLog.entity_type == "user")]

    assert any("e-mail: anna@firma.sk → nova@firma.sk" in s and "upozornenia: zapnuté → vypnuté" in s for s in summaries)


# ---------------------------------------------------------------------
# Príkazy správcu a migrácia
# ---------------------------------------------------------------------

def _manage(monkeypatch, *args):
    import sys

    import manage

    monkeypatch.setattr(sys, "argv", ["manage.py", *args])
    manage.main()


def test_manage_commands(ew, monkeypatch, capsys):
    _manage(monkeypatch, "send-test-email", "Admin@Firma.sk")
    assert "odoslaný na admin@firma.sk" in capsys.readouterr().out
    assert [m["To"] for m in FakeSMTP.sent] == ["admin@firma.sk"]

    FakeSMTP.failures_left = 1
    with pytest.raises(SystemExit):
        _manage(monkeypatch, "send-test-email", "admin@firma.sk")
    assert "Odoslanie zlyhalo" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        _manage(monkeypatch, "send-test-email", "zle")

    for args in (("email-prune",), ("email-prune", "abc"), ("email-prune", "3")):
        with pytest.raises(SystemExit):
            _manage(monkeypatch, *args)

    monkeypatch.delenv("SMTP_HOST")
    for command in ("send-emails", "send-test-email"):
        with pytest.raises(SystemExit):
            _manage(monkeypatch, command, "a@b.sk")

    _manage(monkeypatch, "email-status")
    assert "VYPNUTÉ" in capsys.readouterr().out


# Príkazy si vytvárajú vlastnú appku (v režime TEST_DATABASE_URL by každé
# create_app zmazalo spoločnú testovaciu databázu).
@pytest.mark.sqlite_only
def test_email_status_and_send_emails_commands(monkeypatch, capsys, tmp_path):
    from app.extensions import db
    from app.web.app import create_app

    # Príkazy používajú predvolenú databázu -> dočasne ju nasmerujeme na test.
    import app.web.app as app_module

    real_create_app = app_module.create_app
    monkeypatch.setattr(app_module, "create_app", lambda: real_create_app(database_path=tmp_path / "cmd.db"))

    app = real_create_app(database_path=tmp_path / "cmd.db")

    with app.app_context():
        db.create_all()

        from app.services.email_service import enqueue_email

        enqueue_email("a@firma.sk", "p", "b")

    _manage(monkeypatch, "email-status")
    out = capsys.readouterr().out
    assert "ZAPNUTÉ" in out and "smtp.test:587" in out and "pending: 1" in out
    assert "https://plan.firma.sk" in out

    _manage(monkeypatch, "send-emails")
    assert "Odoslaných: 1" in capsys.readouterr().out
    assert [m["To"] for m in FakeSMTP.sent] == ["a@firma.sk"]


def test_email_migration_roundtrip_and_defaults(tmp_path):
    import sqlalchemy as sa
    from flask_migrate import downgrade, upgrade

    from app.extensions import db
    from app.web.app import create_app

    app = create_app(database_path=tmp_path / "mailmig.db")

    with app.app_context():
        upgrade(revision="a8b9c0d1e243")

        connection = db.session.connection()
        connection.execute(sa.text(
            "INSERT INTO users (username, password_hash, role) VALUES ('stary', 'x', 'admin')"
        ))
        db.session.commit()

        upgrade()

        inspector = sa.inspect(db.engine)
        assert {"email", "notify_email"} <= {c["name"] for c in inspector.get_columns("users")}
        assert "email_outbox" in inspector.get_table_names()

        # Existujúci účet: bez adresy, upozornenia zapnuté (predvolene).
        row = db.session.connection().execute(
            sa.text("SELECT email, notify_email FROM users WHERE username = 'stary'")
        ).one()
        assert tuple(row) == (None, 1)

        # Ukončiť transakciu: PostgreSQL by inak čakal na zámok tabuľky
        # "users" pri jej zmene v downgrade.
        db.session.rollback()

        downgrade(revision="a8b9c0d1e243")
        inspector = sa.inspect(db.engine)
        assert "email_outbox" not in inspector.get_table_names()
        assert "email" not in {c["name"] for c in inspector.get_columns("users")}

        upgrade()
        assert "email_outbox" in sa.inspect(db.engine).get_table_names()
