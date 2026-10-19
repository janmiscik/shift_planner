"""Webová aplikácia Shift Planner - zostavenie aplikácie (``create_app``).

Táto vrstva sa stará výlučne o spracovanie HTTP požiadaviek
(parsovanie vstupov, presmerovania, JSON odpovede). Biznis logika
a výpočty (kolízie smien, týždenné fondy, kalendárna mriežka,
exporty) žijú v ``app/services``.

Rozdelenie webovej vrstvy:
  - ``app/web/core.py``         - blueprint ``main`` a CSRF,
  - ``app/web/permissions.py``  - oprávnenia podľa rolí (zoznamy
                                  ``_*_ENDPOINTS``), kontrola prihlásenia,
  - ``app/web/helpers.py``      - rozsah pobočiek a spoločné pomocné funkcie,
  - ``app/web/routes/*.py``     - routy podľa oblastí (všetky v blueprinte
                                  ``main``, takže názvy endpointov sa
                                  rozdelením nezmenili).

Role: admin (všetko), manager (smeny a absencie na pridelených
pobočkách), employee (vlastný rozpis a export). Rozsah dát podľa
pobočiek rieši ``app/services/access_service.py``.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from flask import (
    Flask,
)
from app.data.database import (
    is_sqlite_uri,
    resolve_database_uri,
)
from app.extensions import db, login_manager, migrate
from app.services.audit_service import (
    init_audit,
    to_local,
)
from app.services.login_throttle import LoginThrottle
from app.web.core import (
    _DEFAULT_SECRET_KEY,
    bp,
    csrf,
)

# Import zaregistruje oprávnenia (before_request, context processor) a
# všetky routy do blueprintu ``main``.
from app.web import permissions, routes  # noqa: F401

# Názvy, ktoré odtiaľto očakávajú testy a ``manage.py``.
from app.web.permissions import (  # noqa: F401
    _COMMON_ENDPOINTS,
    _EMPLOYEE_ALLOWED_ENDPOINTS,
    _EMPLOYEE_BRANCH_VIEW_ENDPOINTS,
    _MANAGER_ALLOWED_ENDPOINTS,
    _OWN_SCHEDULE_ENDPOINTS,
    _PUBLIC_ENDPOINTS,
)

load_dotenv()


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
