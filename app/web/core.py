"""Spoločné objekty webovej vrstvy (blueprint ``main``, CSRF)."""

from flask import (
    Blueprint,
)
from flask_wtf import CSRFProtect


bp = Blueprint("main", __name__)

csrf = CSRFProtect()

from app.web.config_check import DEFAULT_SECRET_KEY as _DEFAULT_SECRET_KEY  # noqa: E402
