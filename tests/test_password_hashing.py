"""Poistka: rýchle hashovanie v testoch (tests/conftest.py) nesmie nikdy
oslabiť hashovanie hesiel v samotnej appke."""

import inspect

from app.services import auth_service


def test_production_code_does_not_choose_a_weak_hash_method():
    source = inspect.getsource(auth_service)

    # Appka volá generate_password_hash(heslo) s bezpečným predvoleným
    # nastavením knižnice (scrypt) - žiadne vlastné "method=" ani
    # iterácie.
    assert "method=" not in source
    assert "pbkdf2" not in source
    assert source.count("generate_password_hash(") >= 5


def test_the_unknown_user_dummy_hash_uses_the_library_default():
    # Fiktívny hash (vyrovnanie času pri neznámom mene) vznikol pri importe,
    # teda ešte pred rýchlou náhradou v testoch.
    assert auth_service._DUMMY_PASSWORD_HASH.startswith("scrypt:")


def test_hashes_created_in_tests_still_verify():
    user = type("U", (), {})()
    user.password_hash = auth_service.generate_password_hash("tajne-heslo-1")

    assert auth_service.verify_password(user, "tajne-heslo-1")
    assert not auth_service.verify_password(user, "ine-heslo")
