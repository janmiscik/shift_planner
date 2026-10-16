"""Konfigurácia databázy.

Samotný prístup k dátam je cez SQLAlchemy (pozri ``app/extensions.py``
a ``app/orm_models.py``). Tento modul určuje, KAM sa pripojiť:

  1. explicitná cesta k SQLite súboru (``create_app(database_path=...)``
     - používajú ju testy, aby mal každý test vlastnú dočasnú DB),
  2. premenná prostredia ``DATABASE_URL`` (napr. PostgreSQL pre
     nasadenie na viac pobočiek),
  3. predvolený SQLite súbor ``app/data/shift_planner.db``.
"""

import os
from pathlib import Path

from sqlalchemy.engine import make_url


DEFAULT_DATABASE_PATH = Path(__file__).parent / "shift_planner.db"


def normalize_database_url(url):
    """Upraví PostgreSQL URL do tvaru s explicitným ovládačom psycopg2.

    Poskytovatelia (Heroku, Railway...) často dávajú ``postgres://`` a
    SQLAlchemy ``postgresql://`` bez ovládača vyberie podľa verzie buď
    psycopg2, alebo psycopg 3 - appka používa ``psycopg2-binary``
    (pozri requirements.txt), preto ho určíme výslovne.
    """

    url = url.strip()

    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]

    return url


def resolve_database_uri(database_path=None):
    """Vráti SQLAlchemy URI podľa poradia popísaného vyššie."""

    if database_path is not None:
        return f"sqlite:///{database_path}"

    env_url = os.environ.get("DATABASE_URL", "").strip()

    if env_url:
        return normalize_database_url(env_url)

    return f"sqlite:///{DEFAULT_DATABASE_PATH}"


def is_sqlite_uri(uri):
    return str(uri).startswith("sqlite")


def is_postgres_uri(uri):
    return str(uri).startswith("postgresql")


def sqlite_path_from_uri(uri):
    """Cesta k súboru z ``sqlite:///...`` URI."""

    return Path(str(uri).replace("sqlite:///", "", 1))


def redact_database_uri(uri):
    """URI bez hesla - bezpečné na vypísanie do konzoly."""

    try:
        return make_url(str(uri)).render_as_string(hide_password=True)
    except Exception:
        return "<neplatné URI>"
