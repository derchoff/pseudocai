"""JSON de métadonnées de la GED : champs conservés, libellés de service, valeurs pseudonymisées."""

import json
from pathlib import Path

import pytest

from pseudo.config import Settings
from pseudo.fields import FieldCatalogError, parse_fields
from pseudo.json_metadata import JsonMetadataError, build_json_text, pseudonymise_json
from pseudo.json_rules import parse_json_rules, parse_path
from pseudo.prompts import LocalPromptSource, PromptRepository
from test_pipeline import FakeDetector, services

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CATALOG = PromptRepository(LocalPromptSource(PROMPTS)).catalog()
RULES = CATALOG.json_rules

# Même structure que les JSON de la GED ; toutes les valeurs sont fictives.
GED = {
    "DOCUMENT": {
        "id": "00000000-0000-0000-0000-000000000001",
        "label": "Acte de deces + Correspondance - 9000001 - 9000002.eml",
        "date_qualification": "2026-02-18 00:00:00.000",
        "nature": "Modification",
        "famille": "Administratif",
    },
    "GED_metadata": {
        "id": "00000000-0000-0000-0000-000000000001",
        "label": "Acte de deces + Correspondance - 9000001 - 9000002.eml",
        "description": "<<= Décès salarié - DUPONT Jean (1000000000001) - Statut GED : Traité 27 février 2026",
        "size": 754854,
        "mimetype": "message/rfc822",
        "creator": "Administrateur QUALIFICATION",
        "created": 1771397790667,
        "modified": 1772172269108,
        "modifier": "Claire MARTIN",
        "ancestors": [
            {"id": "a1", "label": "Année 2026", "type": "periode"},
            {"id": "a3", "label": "DUPONT Jean (11124714)", "type": "client"},
        ],
        "metadatas": {
            "Emetteur du mail": "Paul LEROY <paul.leroy@example.com>",
            "Statut": "traité",
            "Nature": ["Acte de décès", "Correspondance"],
            "Domaine": [],
            "Gestionnaire dédié PRESTA": "Gestion Prestations Individuelles",
            "Gestionnaire dédié BDD": "Gestion Administrative Individuelle",
            "Gestionnaire dédié Tiers, Trésoreries": [],
            "Gestionnaire dédié Qualité": "Julie BERNARD",
            "Souscripteur": "EXEMPLE INDUSTRIES (9968108)",
            "Num interne contrat": "10394068",
            "Emetteur, Destinataire": "Assuré",
            "Qualificateur": "QUALIF_8",
        },
    },
    "cafeteria_decompte": [],
}
NAMES = {"DUPONT": "NOM", "Jean": "PRENOM", "MARTIN": "NOM", "Claire": "PRENOM", "LEROY": "NOM",
         "Paul": "PRENOM", "BERNARD": "NOM", "Julie": "PRENOM", "paul.leroy@example.com": "EMAIL"}


@pytest.fixture
def settings(monkeypatch):
    """Mode substitution : valeurs fictives réalistes."""
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    return Settings.from_env()


def run(settings, data=GED, detector=None):
    """JSON pseudonymisé avec le vrai référentiel ; renvoie (résultat, JSON relu, détecteur)."""
    detector = detector or FakeDetector(NAMES, only_first=False)
    result = pseudonymise_json(json.dumps(data).encode(), settings, services(detector=detector, fields=lambda: CATALOG), RULES)
    return result, json.loads(result.output) if result.output else None, detector


def test_referential_declares_kept_fields_and_service_labels():
    assert ("DOCUMENT", "*") in RULES.keep and ("GED_metadata", "ancestors", "[]", "id") in RULES.keep
    assert RULES.service_prefixes == ("Administrateur", "Gestion")
    assert {"administrateur", "gestion"} <= {w.lower() for w in CATALOG.exceptions.words}


def test_kept_fields_are_copied_unchanged_and_others_pseudonymised(settings):
    result, out, _ = run(settings)
    assert result.status == "ok"
    meta = out["GED_metadata"]["metadatas"]
    # Champs conservés : identiques, y compris les tableaux et les nombres.
    assert out["DOCUMENT"] == GED["DOCUMENT"] and out["cafeteria_decompte"] == []
    for key in ("Statut", "Nature", "Domaine", "Souscripteur", "Num interne contrat", "Emetteur, Destinataire"):
        assert meta[key] == GED["GED_metadata"]["metadatas"][key]
    assert out["GED_metadata"]["size"] == 754854 and out["GED_metadata"]["ancestors"][1]["id"] == "a3"
    # Personnes : remplacées partout, avec la même valeur fictive pour le même nom.
    text = json.dumps(out, ensure_ascii=False)
    for secret in ("DUPONT", "MARTIN", "LEROY", "BERNARD", "paul.leroy@example.com"):
        assert secret not in text
    fake = out["GED_metadata"]["ancestors"][1]["label"].split()[0]
    assert fake in out["GED_metadata"]["description"]
    assert out["GED_metadata"]["description"].startswith("<<= Décès salarié - ")
    assert meta["Emetteur du mail"].endswith("@example.com>")


def test_structure_and_order_are_identical(settings):
    _, out, _ = run(settings)

    def shape(node):
        if isinstance(node, dict):
            return [(k, shape(v)) for k, v in node.items()]
        if isinstance(node, list):
            return [shape(v) for v in node]
        return type(node).__name__

    assert shape(out) == shape(GED)


def test_service_labels_only_in_gestionnaire_fields(settings):
    # Même si l'agent se trompe sur « Individuelles », un libellé de service n'est pas analysé.
    detector = FakeDetector({**NAMES, "Individuelles": "NOM", "Individuelle": "NOM"}, only_first=False)
    _, out, _ = run(settings, detector=detector)
    meta = out["GED_metadata"]["metadatas"]
    assert meta["Gestionnaire dédié PRESTA"] == "Gestion Prestations Individuelles"
    assert meta["Gestionnaire dédié BDD"] == "Gestion Administrative Individuelle"
    assert meta["Gestionnaire dédié Qualité"] != "Julie BERNARD"  # une personne : pseudonymisée
    # Hors des champs « Gestionnaire dédié … », la règle ne s'applique pas : la valeur est analysée.
    built = build_json_text(GED, RULES)
    analysed = {line.text for line in built.content.lines()}
    assert "GED_metadata.creator : Administrateur QUALIFICATION" in analysed


def test_only_unkept_values_reach_the_agents(settings):
    _, _, detector = run(settings)
    built = build_json_text(GED, RULES)
    texts = " ".join(line.text for line in built.content.lines())
    assert "Souscripteur" not in texts and "Num interne contrat" not in texts and "DOCUMENT" not in texts
    assert "GED_metadata.ancestors[1].label : DUPONT Jean (11124714)" in texts
    assert built.kept > 10 and len(built.values) == 9


def test_path_is_never_modified_and_multiline_values_roundtrip(settings):
    data = {"GED_metadata": {"description": "Ligne 1 DUPONT\n\nLigne 3 Jean"}}
    detector = FakeDetector({"description": "NOM", "DUPONT": "NOM", "Jean": "PRENOM"}, only_first=False)
    _, out, _ = run(settings, data, detector)
    value = out["GED_metadata"]["description"]
    assert value.count("\n") == 2 and value.startswith("Ligne 1 ") and "DUPONT" not in value and "Jean" not in value
    assert list(out["GED_metadata"]) == ["description"]  # le nom de clé n'est jamais remplacé


def test_json_without_values_to_analyse_skips_the_agents(settings):
    detector = FakeDetector(NAMES)
    detector.detect = lambda lines: pytest.fail("aucun appel attendu")
    result, out, _ = run(settings, {"DOCUMENT": {"label": "x.pdf"}, "GED_metadata": {"size": 3}}, detector)
    assert result.status == "ok" and out == {"DOCUMENT": {"label": "x.pdf"}, "GED_metadata": {"size": 3}}


def test_invalid_json_is_rejected(settings):
    with pytest.raises(JsonMetadataError):
        pseudonymise_json(b"{pas du json", settings, services(), RULES)


def test_path_notation_and_errors():
    assert parse_path("GED_metadata.ancestors[].id") == ("GED_metadata", "ancestors", "[]", "id")
    rules = parse_json_rules("## Champs JSON à ne jamais modifier\n- `a.b`\n- `c[].*`\n")
    assert rules.is_kept(("a", "b")) and rules.is_kept(("a", "b", "[]")) and rules.is_kept(("c", "[]", "x"))
    assert not rules.is_kept(("a", "bb")) and not rules.is_kept(("c",))
    with pytest.raises(FieldCatalogError, match="chemin JSON"):
        parse_fields("## NOM — Nom\n\n## Champs JSON à ne jamais modifier\n- `a..b`\n")
