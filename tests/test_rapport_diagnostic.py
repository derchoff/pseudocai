"""Rapport détaillé (REPORT_DETAIL=diagnostic) : contenu de chaque étape, jamais de valeur d'origine."""

import json

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.detection.llm import EntityAgent
from pseudo.models import DocumentContent, Entity, Line, PageContent, Word
from pseudo.pipeline import run_pipeline
from pseudo.spans import explain_unresolved, resolve_entities

from test_fields import RecordingLlm, current_champs, write_prompts
from test_pipeline import FakeVerifier, make_text_pdf, services


@pytest.fixture
def settings(monkeypatch):
    """Réglages par défaut (le rapport détaillé est le défaut)."""
    for key in ("FORCE_OCR", "VERIFICATION_ROUNDS", "OCR_NATIVE_PAGES", "REPORT_DETAIL"):
        monkeypatch.delenv(key, raising=False)
    return Settings.from_env()


def doc_of(*texts: str) -> DocumentContent:
    """Document d'une page dont les lignes sont les textes donnés."""
    lines = [Line.build(f"p1-l{i}", 0, [Word(t, (0, 0, 1, 1)) for t in text.split()]) for i, text in enumerate(texts, 1)]
    return DocumentContent(pages=[PageContent(index=0, source="native", lines=lines)])


def test_diagnostic_is_the_default_and_standard_removes_it(settings, monkeypatch):
    assert settings.report_detail == "diagnostic"
    report = run_pipeline(make_text_pdf(), settings, services()).report
    diag = report["diagnostic"]
    assert diag["extraction"][0]["source"] == "native"
    assert diag["detection"]["regex"] >= 1
    assert diag["localisation"]["methodes"]["exacte"] >= 1
    assert diag["verification"][0]["tour"] == 1
    assert {"extraction", "detection", "verification", "rendu"} <= set(diag["durees_s"])

    monkeypatch.setenv("REPORT_DETAIL", "standard")
    assert "diagnostic" not in run_pipeline(make_text_pdf(), Settings.from_env(), services()).report


def test_unknown_report_detail_is_refused(monkeypatch):
    monkeypatch.setenv("REPORT_DETAIL", "bavard")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_unlocated_entity_is_explained_without_its_value(settings):
    detector = services().detector
    detector.detect = lambda lines: [Entity("p1-l2", "INTROUVABLE", "NOM")]
    result = run_pipeline(make_text_pdf(), settings, services(detector=detector))
    assert "entites_non_localisees" in result.reasons
    loc = result.report["diagnostic"]["localisation"]
    assert loc["echecs_par_type"] == {"NOM": 1}
    failure = loc["echecs"][0]
    assert failure["ligne"] == "p1-l2" and failure["methode"] == "echec"
    assert failure["longueur"] == len("INTROUVABLE") and failure["mots"] == 1
    assert "INTROUVABLE" not in json.dumps(result.report)


def test_value_split_over_two_lines_is_reported():
    doc = doc_of("Acte 123 dressé à Lyon, le vingt-trois mars mil neuf", "cent cinquante-deux, est né")
    e = Entity("p1-l1", "vingt-trois mars mil neuf cent cinquante-deux", "DATE_NAISSANCE")
    located: list[dict] = []
    _, unresolved = resolve_entities([e], doc, 85, located)
    assert unresolved == [e]
    assert located[0]["trouvee_sur_2_lignes"] is True
    assert located[0]["trouvee_sans_accents_ponctuation"] is False


def test_hyphenated_word_and_accents_are_reported():
    doc = doc_of("fille de Marie-Thé-", "rèse DÉSIRÉ, née d’Ornano")
    assert explain_unresolved(Entity("p1-l1", "Marie-Thérèse", "PRENOM"), doc)["trouvee_sur_2_lignes"] is True
    accents = explain_unresolved(Entity("p1-l2", "DESIRE", "NOM"), doc)
    assert accents["trouvee_sans_accents_ponctuation"] is True and accents["ligne_connue"] is True
    assert explain_unresolved(Entity("p1-l2", "d'Ornano", "NOM"), doc)["trouvee_sans_accents_ponctuation"] is True


def test_partial_approximate_match_is_visible():
    # Ligne courte : la recherche approchée ne retrouve que le début de la date, coupée sur 2 lignes.
    doc = doc_of("le vingt-trois mars mil neuf", "cent cinquante-deux, est né")
    e = Entity("p1-l1", "vingt-trois mars mil neuf cent cinquante-deux", "DATE_NAISSANCE")
    located: list[dict] = []
    resolve_entities([e], doc, 85, located)
    assert located[0]["methode"] == "approchee"
    assert located[0]["longueur_masquee"] < located[0]["longueur"]


def test_localisation_methods_are_named():
    doc = doc_of("Nom : DUPONT Jean", "Signature D U PONT")
    entities = [
        Entity("p1-l1", "dupont", "NOM"),  # exacte (casse ignorée)
        Entity("p1-l2", "DUPONT", "NOM"),  # espaces ajoutés par l'OCR
        Entity("p9-l9", "Jean", "PRENOM"),  # mauvaise ligne : trouvée ailleurs
    ]
    located: list[dict] = []
    resolve_entities(entities, doc, 85, located)
    assert [e["methode"] for e in located] == ["exacte", "espaces", "autre_ligne"]


def test_each_verification_round_is_reported_without_values(settings):
    verifier = FakeVerifier(residual_text="12 rue des Lilas 75011 PARIS")
    result = run_pipeline(make_text_pdf(), settings, services(verifier=verifier))
    rounds = result.report["diagnostic"]["verification"]
    assert [r["tour"] for r in rounds] == [1, 2]
    first, second = rounds
    assert first["residus"] == [{"type": "ADRESSE", "ligne": "p1-l4"}]
    assert first["a_trouve_du_nouveau"] is True and first["nouveaux_caracteres_masques"] > 0
    assert "p1-l4" in first["lignes_completees"]
    assert second["residus_signales"] == 0 and second["a_trouve_du_nouveau"] is False
    assert "Lilas" not in json.dumps(result.report)


def test_agent_reports_calls_tokens_and_dropped_entities(tmp_path):
    repo = write_prompts(tmp_path, current_champs())
    llm = RecordingLlm(
        [
            {"line_id": "p1-l1", "text": "DUPONT", "type": "NOM", "group": "", "confidence": 0.9},
            {"line_id": "p1-l1", "text": "Jean", "type": "PRENOM", "group": "", "confidence": 0.1},
            {"line_id": "p7-l1", "text": "Jean", "type": "PRENOM", "group": "", "confidence": 0.9},
        ]
    )
    agent = EntityAgent(llm, repo.detection, source="llm", chunk_chars=10000, overlap_lines=0, min_confidence=0.3)
    agent.run([("p1-l1", "Nom : DUPONT Jean")])
    stats = agent.last_stats
    assert stats["appels"] == 1 and stats["jetons_entree"] == 10
    assert stats["entites_rendues"] == 3 and stats["ecartees_confiance"] == 1 and stats["ligne_inconnue"] == 1
