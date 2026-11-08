"""Kontrola produkčnej konfigurácie.

Varovanie vo výpise nechráni pred nasadením s nebezpečným nastavením, preto
sa v PRODUKČNOM režime (``APP_ENV=production``, vždy aj pri
``python manage.py serve``) appka s nebezpečnou konfiguráciou ODMIETNE
spustiť:

  - ``SECRET_KEY`` chýba, je predvolený/ukážkový, krátky (< 32 znakov) alebo
    nevyzerá náhodne (veľmi málo rôznych znakov),
  - appka beží za HTTPS proxy (``BEHIND_PROXY=1``), ale
    ``SESSION_COOKIE_SECURE`` nie je ``1`` (cookie by sa posielala aj
    nešifrovane),
  - dôveryhodný proxy je nastavený na "všetkých" (``TRUSTED_PROXY=*``) -
    ktokoľvek by si vedel podvrhnúť IP adresu a obísť limit pokusov,
  - (len ``serve``) server počúva na sieťovej adrese bez HTTPS.

Výnimka pre sieť bez HTTPS: ``ALLOW_INSECURE_HTTP=1`` (heslá potom idú
po sieti nešifrovane - len pre uzavretú internú sieť).

Vo vývoji (bez ``APP_ENV=production``) sa nič neodmieta, len vypíše
upozornenie.
"""

import os

DEFAULT_SECRET_KEY = "dev-docasny-kluc-zmen-v-produkcii"

# Hodnoty, ktoré sú verejne známe (predvolená v kóde, ukážka v
# .env.example, bežné zástupné texty) - nikdy nie sú tajné.
KNOWN_PLACEHOLDERS = {
    DEFAULT_SECRET_KEY,
    "sem-vloz-vygenerovany-nahodny-retazec",
    "changeme",
    "change-me",
    "secret",
    "secret-key",
    "your-secret-key",
}

MIN_SECRET_KEY_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 10

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class ConfigError(RuntimeError):
    """Nebezpečná produkčná konfigurácia - appka sa nespustí."""


def weak_secret_reason(key):
    """Dôvod, prečo ``key`` nie je vhodný ako ``SECRET_KEY`` (inak
    ``None``)."""

    if not key or not key.strip():
        return "nie je nastavený"

    if key.strip().lower() in KNOWN_PLACEHOLDERS:
        return (
            "je predvolená alebo ukážková hodnota (verejne známa), nie "
            "vlastný tajný kľúč"
        )

    if len(key) < MIN_SECRET_KEY_LENGTH:
        return (
            f"je príliš krátky ({len(key)} znakov, treba aspoň "
            f"{MIN_SECRET_KEY_LENGTH})"
        )

    if len(set(key)) < MIN_DISTINCT_CHARACTERS:
        return "nevyzerá náhodne (veľmi málo rôznych znakov)"

    return None


def is_production(environ=None):
    environ = os.environ if environ is None else environ

    return environ.get("APP_ENV", "").strip().lower() == "production"


def production_problems(environ=None, check_listening=False):
    """Zoznam problémov konfigurácie pre produkciu (prázdny = v poriadku).

    ``check_listening`` pridá kontrolu adresy, na ktorej počúva server
    (používa ho ``manage.py serve``)."""

    environ = os.environ if environ is None else environ
    problems = []

    reason = weak_secret_reason(environ.get("SECRET_KEY", ""))

    if reason:
        problems.append(f"SECRET_KEY {reason}.")

    insecure_allowed = environ.get("ALLOW_INSECURE_HTTP", "") == "1"
    behind_proxy = environ.get("BEHIND_PROXY", "") == "1"
    secure_cookie = environ.get("SESSION_COOKIE_SECURE", "") == "1"

    if environ.get("TRUSTED_PROXY", "").strip() == "*":
        problems.append(
            "TRUSTED_PROXY=* dôveruje hlavičkám od ľubovoľnej adresy - "
            "nastav presnú adresu reverzného proxy (predvolene 127.0.0.1)."
        )

    if behind_proxy and not secure_cookie and not insecure_allowed:
        problems.append(
            "BEHIND_PROXY=1 (HTTPS končí na proxy), ale SESSION_COOKIE_SECURE "
            "nie je 1 - prihlasovacia cookie by sa mohla posielať "
            "nešifrovane. Nastav SESSION_COOKIE_SECURE=1."
        )

    if check_listening and not insecure_allowed:
        host = environ.get("HOST", "127.0.0.1")

        if host not in LOOPBACK_HOSTS and not behind_proxy and not secure_cookie:
            problems.append(
                f"server počúva na {host} bez HTTPS - heslá by sa posielali "
                "nešifrovane. Použi reverzný proxy s HTTPS (HOST=127.0.0.1, "
                "BEHIND_PROXY=1, SESSION_COOKIE_SECURE=1; pozri DEPLOYMENT.md)."
            )

    return problems


def explain(problems):
    """Text chyby pre používateľa."""

    lines = ["Appka sa v produkčnom režime nespustí s touto konfiguráciou:"]
    lines += [f"  - {problem}" for problem in problems]
    lines += [
        "",
        "Vygeneruješ kľúč: python -c \"import secrets; print(secrets.token_hex(32))\"",
        "Ak naozaj bežíš len v uzavretej internej sieti bez HTTPS, nastav "
        "ALLOW_INSECURE_HTTP=1 (heslá potom idú po sieti nešifrovane).",
    ]

    return "\n".join(lines)


def validate_production_config(environ=None, check_listening=False):
    """Vyhodí ``ConfigError``, ak je konfigurácia pre produkciu
    nebezpečná."""

    problems = production_problems(environ, check_listening=check_listening)

    if problems:
        raise ConfigError(explain(problems))
