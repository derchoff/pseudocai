"""Le point d'entrée enregistre les fonctions du rôle demandé, et seulement celles-là."""

import importlib
import sys

import pytest


def registered(monkeypatch, role):
    """Noms des Functions enregistrées par function_app pour le rôle donné (None = variable absente)."""
    if role is None:
        monkeypatch.delenv("FUNCTION_APP_ROLE", raising=False)
    else:
        monkeypatch.setenv("FUNCTION_APP_ROLE", role)
    sys.modules.pop("function_app", None)
    module = importlib.import_module("function_app")
    return sorted(f.get_function_name() for f in module.app.get_functions())


def test_main_role_is_the_default(monkeypatch):
    assert registered(monkeypatch, None) == ["pseudonymise_job", "route_incoming", "split_mail"]
    assert registered(monkeypatch, "main") == ["pseudonymise_job", "route_incoming", "split_mail"]


def test_images_role_registers_only_image_preparation(monkeypatch):
    assert registered(monkeypatch, "images") == ["prepare_image"]


def test_unknown_role_fails_fast(monkeypatch):
    with pytest.raises(RuntimeError, match="FUNCTION_APP_ROLE"):
        registered(monkeypatch, "autre")
