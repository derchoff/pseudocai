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


@pytest.mark.parametrize("role", ["main", "images"])
def test_output_bindings_have_types_azure_accepts(monkeypatch, role):
    """Reproduit le contrôle fait par Azure au chargement : le type d'une sortie (func.Out[T]) doit
    être accepté par le convertisseur de la liaison. Une seule sortie refusée et AUCUNE Function de
    l'app n'est chargée (les messages restent dans les files)."""
    import typing

    import azure.functions as func
    from azure.functions.decorators.core import BindingDirection
    from azure.functions.meta import _ConverterMeta

    monkeypatch.setenv("FUNCTION_APP_ROLE", role)
    sys.modules.pop("function_app", None)
    app = importlib.import_module("function_app").app
    for function in app.get_functions():
        hints = typing.get_type_hints(function.get_user_function())
        for binding in function.get_bindings():
            if getattr(binding.direction, "value", binding.direction) != BindingDirection.OUT.value:
                continue
            annotation = hints[binding.name]
            assert typing.get_origin(annotation) is func.Out, (function.get_function_name(), binding.name)
            pytype = typing.get_args(annotation)[0]
            converter = _ConverterMeta.get(binding.type)
            ok = isinstance(pytype, type) and converter.check_output_type_annotation(pytype)
            assert ok, f"{function.get_function_name()}.{binding.name} : Out[{pytype}] refusé par Azure"
