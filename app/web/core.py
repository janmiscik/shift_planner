"""Spoločné objekty webovej vrstvy (blueprint ``main``, CSRF)."""

from flask import (
    Blueprint,
)
from flask_wtf import CSRFProtect


bp = Blueprint("main", __name__)

csrf = CSRFProtect()

_DEFAULT_SECRET_KEY = "dev-docasny-kluc-zmen-v-produkcii"
