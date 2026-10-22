"""Prihlásenie, odhlásenie a zmena vlastného hesla."""

from urllib.parse import urlparse
from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
)
from flask_login import (
    current_user,
    login_user,
    logout_user,
)
from app.orm_models import ROLE_EMPLOYEE
from app.services.auth_service import (
    change_own_password,
    authenticate,
    set_user_email,
)
from app.services.email_service import (
    normalize_email,
    send_message,
    smtp_configured,
)
from app.web.forms import (
    AccountSettingsForm,
    ChangePasswordForm,
    LoginForm,
)

from app.web.core import bp


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


@bp.route("/account/settings", methods=["GET", "POST"])
def account_settings_page():
    """Vlastný e-mail a súhlas s e-mailovými upozorneniami (každá
    rola)."""

    form = AccountSettingsForm()

    if request.method == "GET":
        form.email.data = current_user.email or ""
        form.notify_email.data = bool(current_user.notify_email)

    if form.validate_on_submit():
        email, error = normalize_email(form.email.data)

        if error:
            flash(error, "error")
        else:
            set_user_email(current_user.id, email, form.notify_email.data)
            flash("Nastavenia boli uložené.", "success")

            return redirect("/account/settings")

    return render_template(
        "account_settings.html",
        form=form,
        email_enabled=smtp_configured(),
    )


@bp.route("/account/settings/test-email", methods=["POST"])
def send_test_email_page():
    """Odošle skúšobný e-mail na adresu prihláseného používateľa."""

    if not smtp_configured():
        flash(
            "E-mailové upozornenia nie sú na serveri nastavené (SMTP).",
            "error",
        )
    elif not current_user.email:
        flash("Najprv ulož svoju e-mailovú adresu.", "error")
    else:
        address = current_user.email

        try:
            send_message(
                address,
                "Skúšobný e-mail - Shift Planner",
                "Ak čítaš túto správu, e-mailové upozornenia fungujú.\n\n"
                "-- Shift Planner",
            )
        except Exception as error:
            flash(f"Odoslanie zlyhalo: {error}", "error")
        else:
            flash(f"Skúšobný e-mail bol odoslaný na {address}.", "success")

    return redirect("/account/settings")

