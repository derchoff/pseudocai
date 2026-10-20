"""Mots à ne jamais pseudonymiser (« devis », « Dr »…) et même valeur fictive quelle que soit la casse."""

from pathlib import Path

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.exceptions import parse_exceptions
from pseudo.models import Span
from pseudo.pipeline import run_pipeline
from pseudo.prompts import LocalPromptSource, PromptRepository
from test_pipeline import FakeDetector, TruthOcr, output_text, services

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CATALOG = PromptRepository(LocalPromptSource(PROMPTS)).catalog()


def pdf(*lines: str) -> bytes:
    """PDF natif d'une page avec ces lignes."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(lines):
        page.insert_text((72, 72 + 24 * i), text, fontsize=12)
    return doc.tobytes()


class SpanLanguage:
    """Azure AI Language simulé : renvoie des zones fixées (texte cherché dans chaque ligne)."""

    def __init__(self, values: dict[str, str]):
        self.values = values

    def detect(self, lines):
        """Une zone par occurrence des valeurs données."""
        spans = []
        for line in lines:
            for value, etype in self.values.items():
                start = line.text.find(value)
                if start >= 0:
                    spans.append(Span(line.id, start, start + len(value), etype, "language"))
        return spans


@pytest.fixture
def settings(monkeypatch):
    """Mode substitution : les valeurs fictives sont écrites dans le document."""
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    return Settings.from_env()


def run(settings, document: bytes, detector, language=None):
    """Pipeline avec le vrai référentiel des champs (mots protégés compris)."""
    svc = services(detector=detector, language=language, ocr=TruthOcr(document), fields=lambda: CATALOG)
    return run_pipeline(document, settings, svc)


def test_referential_lists_the_protected_words():
    words = {w.lower() for w in CATALOG.exceptions.words}
    assert {"devis", "dr", "docteur", "facture"} <= words


def test_devis_is_never_replaced_even_when_detected_and_propagated(settings):
    document = pdf("DEVIS n° 2026-118", "Devis pour M. DUPONT Jean", "Merci de signer le devis.")
    # L'agent se trompe : « DEVIS » pris pour un prénom ; la propagation l'étendrait à « devis ».
    result = run(settings, document, FakeDetector({"DEVIS": "PRENOM", "DUPONT": "NOM", "Jean": "PRENOM"}))
    published = output_text(result.output)
    assert published.count("DEVIS") == 1 and "Devis pour" in published and "le devis." in published
    assert "DUPONT" not in published and "Jean" not in published


def test_title_is_kept_when_a_detector_includes_it(settings):
    document = pdf("Dr DUPONT, chirurgien-dentiste", "Docteur Martin LEROY")
    language = SpanLanguage({"Dr DUPONT": "PERSONNE", "Docteur Martin LEROY": "PERSONNE"})
    result = run(settings, document, FakeDetector({}), language=language)
    published = output_text(result.output)
    words = published.split()
    assert "Dr" in words and "Docteur" in words  # titres conservés
    assert "DUPONT" not in published and "LEROY" not in published and "Martin" not in published
    assert all(o["valeur_fictive"].split()[0] not in ("Dr", "Docteur") for o in result.occurrences)


def test_same_value_in_different_cases_gets_the_same_surrogate(settings):
    # « DUPONT » suit une civilité, « Dupont » non : l'indice de genre diffère, la valeur fictive non.
    document = pdf("Patient : Mme DUPONT", "Signé : Dupont", "dupont")
    result = run(settings, document, FakeDetector({"DUPONT": "NOM", "Dupont": "NOM", "dupont": "NOM"}, only_first=False))
    fakes = [o["valeur_fictive"] for o in result.occurrences if o["type"] == "NOM"]
    assert len(fakes) == 3
    assert fakes[0] == fakes[0].upper() and fakes[2] == fakes[2].lower()
    assert len({f.casefold() for f in fakes}) == 1  # une seule valeur fictive, dans 3 casses


def test_trim_and_drop_rules():
    exc = parse_exceptions("## Mots à ne jamais pseudonymiser\n- **Titres** : Dr, Docteur\n- **Mots** : devis\n")
    assert exc.trim_words("Dr DUPONT") == (3, 9)
    assert exc.trim_words("DUPONT Docteur") == (0, 6)
    assert exc.trim_words("DEVIS") is None and exc.trim_words("Dr.") is None
    assert exc.trim_words("Jean DEVISSE") == (0, 12)  # un mot qui contient « devis » n'est pas touché


def test_referential_lists_the_form_labels():
    words = {w.lower() for w in CATALOG.exceptions.words}
    assert {"nom", "noms", "prénom", "prénoms", "prenom", "prenoms"} <= words


def test_form_label_prenom_is_never_replaced_even_when_detected_and_propagated(settings):
    # Acte de naissance : l'agent prend le libellé « Prénom » (case vide) pour un prénom ; la
    # propagation l'étendait à tous les « Prénom » du document, remplacés par le même prénom fictif.
    document = pdf("ENFANT : Prénom", "Prénom : CHLOÉ", "Nom : MARTIN", "PERE : Prénom Jean")
    detector = FakeDetector({"Prénom": "PRENOM", "CHLOÉ": "PRENOM", "MARTIN": "NOM", "Jean": "PRENOM"})
    result = run(settings, document, detector)
    published = output_text(result.output)
    assert published.count("Prénom") == 3 and "Nom :" in published
    assert "CHLOÉ" not in published and "MARTIN" not in published and "Jean" not in published


def test_form_label_is_trimmed_with_its_colon_when_a_detector_includes_it(settings):
    document = pdf("Prénom : CHLOÉ", "Nom: DUPONT", "Bulletin d'adhésion signé le 3 mars.")
    language = SpanLanguage({"Prénom : CHLOÉ": "PERSONNE", "Nom: DUPONT": "PERSONNE"})
    result = run(settings, document, FakeDetector({}), language=language)
    published = output_text(result.output)
    assert "Prénom :" in published and "Nom:" in published  # libellés et deux-points conservés
    assert "CHLOÉ" not in published and "DUPONT" not in published


def test_trim_words_skips_the_label_and_its_punctuation():
    exceptions = CATALOG.exceptions
    assert exceptions.trim_words("Prénom") is None
    assert exceptions.trim_words("Prénom : CHLOÉ") == (9, 14)
    assert exceptions.trim_words("Nom: DUPONT") == (5, 11)
    assert exceptions.trim_words("Dr DUPONT") == (3, 9)
