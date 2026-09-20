"""Cesta k SQLite databáze.

Samotný prístup k dátam je od tejto verzie cez SQLAlchemy (pozri
``app/extensions.py`` a ``app/orm_models.py``). Tento modul drží už
len cestu k súboru databázy - používa sa ako predvolená hodnota pri
vytváraní appky (``create_app()``), keď sa cesta nezadá explicitne
(napr. v testoch, kde každý test dostane vlastný dočasný súbor).
"""

from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).parent / "shift_planner.db"
