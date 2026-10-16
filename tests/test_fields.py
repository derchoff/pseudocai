"""Le référentiel Markdown pilote les types de champs, sans liste en dur dans le code."""

from pathlib import Path

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.detection.llm import EntityAgent
from pseudo.fields import FieldCatalogError, parse_fields
from pseudo.models import Entity
from pseudo.pipeline import Services, run_pipeline
from pseudo.prompts import LocalPromptSource, PromptRepository
from pseudo.pseudonymizer import Pseudonymizer, ValueCipher

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

IBAN_SECTION = """
## IBAN — Numéro de compte bancaire

- **Normalisation** : alphanumerique
- **Définition** : identifiant international de compte bancaire, commence par FR76.
"""


def write_prompts(tmp_path: Path, champs: str) -> PromptRepository:
    """Copie les prompts d'agents dans tmp_path avec le référentiel des champs donné."""
    for name in ("agent-detection.md", "agent-verification.md"):
        (tmp_path / name).write_text((PROMPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "champs-a-pseudonymiser.md").write_text(champs, encoding="utf-8")
    return PromptRepository(LocalPromptSource(tmp_path))


def current_champs() -> str:
    """Contenu actuel de prompts/champs-a-pseudonymiser.md."""
    return (PROMPTS / "champs-a-pseudonymiser.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- lecture du référentiel


def test_current_referential_declares_the_ten_fields():
    catalog = PromptRepository(LocalPromptSource(PROMPTS)).catalog()
    assert catalog.types == (
        "NOM", "PRENOM", "DATE_NAISSANCE", "NIR", "EMAIL", "TELEPHONE", "ADRESSE",
        "IMMATRICULATION", "NUMERO_VIN", "NUMERO_FORMULE",
    )
    # Champs de carte grise : même plaque écrite différemment -> même jeton ; valeur fictive de même forme.
    for field in ("IMMATRICULATION", "NUMERO_VIN", "NUMERO_FORMULE"):
        assert catalog.strategy(field) == "alphanumerique" and catalog.substitution(field) == "generique"
    assert catalog.strategy("NIR") == "alphanumerique"
    assert catalog.strategy("TELEPHONE") == "telephone"
    assert catalog.strategy("DATE_NAISSANCE") == "date"
    assert catalog.strategy("NOM") == "texte"


def test_new_field_is_picked_up_from_markdown_only(tmp_path):
    repo = write_prompts(tmp_path, current_champs() + IBAN_SECTION)
    prompt = repo.detection()
    assert "IBAN" in prompt.catalog.types
    assert prompt.catalog.strategy("IBAN") == "alphanumerique"
    # La liste des types est injectée dans le prompt et les balises ont disparu.
    assert "`IBAN`" in prompt.text
    assert "{{" not in prompt.text
    # Les notes aux mainteneurs (commentaire HTML) ne sont pas envoyées au LLM.
    assert "NOTE POUR LES MAINTENEURS" not in prompt.text


def test_section_without_normalisation_defaults_to_texte():
    catalog = parse_fields("## PASSEPORT_TITULAIRE — Titulaire du passeport\n- **Définition** : x\n")
    assert catalog.strategy("PASSEPORT_TITULAIRE") == "texte"


def test_ordinary_headings_are_not_fields():
    catalog = parse_fields("## Règles transverses\ntexte\n## NOM — Nom\n")
    assert catalog.types == ("NOM",)


@pytest.mark.parametrize(
    "markdown, message",
    [
        ("## Iban — Compte bancaire\n", "MAJUSCULES"),
        ("## NOM — Nom\n## NOM — Encore\n", "plusieurs fois"),
        ("## IBAN — Compte\n- **Normalisation** : alphanum\n", "inconnue"),
        ("# Titre\nrien\n", "aucun champ"),
    ],
)
def test_invalid_referential_is_rejected(markdown, message):
    with pytest.raises(FieldCatalogError, match=message):
        parse_fields(markdown)


def test_commented_example_does_not_declare_a_field(tmp_path):
    repo = write_prompts(tmp_path, "<!--\n## EXEMPLE — ne pas lire\n-->\n## NOM — Nom\n")
    assert repo.catalog().types == ("NOM",)


# ---------------------------------------------------------------- agent IA


class RecordingLlm:
    """Modèle simulé qui note les schémas reçus et renvoie des entités fixées."""

    def __init__(self, entities):
        """entities : entités renvoyées à chaque appel."""
        self.entities = entities
        self.schemas = []

    def run(self, system_prompt, user_content, schema, schema_name="entities"):
        """Note le schéma et renvoie les entités fixées."""
        self.schemas.append(schema)
        return {"entities": self.entities}

    def run_with_usage(self, system_prompt, user_content, schema, schema_name="entities"):
        """Comme run, avec une consommation fixée."""
        return self.run(system_prompt, user_content, schema, schema_name), {"jetons_entree": 10, "duree_s": 0.1}


def test_agent_schema_and_filter_follow_the_referential(tmp_path):
    repo = write_prompts(tmp_path, current_champs() + IBAN_SECTION)
    llm = RecordingLlm(
        [
            {"line_id": "p1-l1", "text": "FR76 3000 6000 0112", "type": "IBAN", "group": "", "confidence": 0.9},
            {"line_id": "p1-l1", "text": "x", "type": "INCONNU", "group": "", "confidence": 0.9},
        ]
    )
    agent = EntityAgent(llm, repo.detection, source="llm", chunk_chars=10000, overlap_lines=0, min_confidence=0)
    entities = agent.run([("p1-l1", "IBAN : FR76 3000 6000 0112")])
    enum = llm.schemas[0]["properties"]["entities"]["items"]["properties"]["type"]["enum"]
    assert "IBAN" in enum and "NOM" in enum
    assert [(e.type, e.text) for e in entities] == [("IBAN", "FR76 3000 6000 0112")]


# ---------------------------------------------------------------- jetons


def test_new_field_uses_its_normalisation_for_tokens(tmp_path):
    """Deux écritures d'un même IBAN -> même jeton, grâce à la normalisation du référentiel."""
    repo = write_prompts(tmp_path, current_champs() + IBAN_SECTION)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "IBAN : FR76 3000 6000 0112", fontsize=12)
    page.insert_text((72, 100), "Rappel IBAN FR7630006000-0112", fontsize=12)

    class Detector:
        """Détecteur simulé : une écriture différente du même IBAN sur chaque ligne."""
        prompt_sha256 = "x"

        def detect(self, lines):
            """Les deux IBAN, sur les deux premières lignes."""
            return [Entity(lines[0].id, "FR76 3000 6000 0112", "IBAN"), Entity(lines[1].id, "FR7630006000-0112", "IBAN")]

    class NoOcr:
        """OCR simulé qui ne lit rien."""

        def ocr(self, pdf_bytes, doc, page_indexes):
            """Aucune page lue."""
            return []

    pseudonymizer = Pseudonymizer(b"k" * 32)
    services = Services(
        detector=Detector(),
        pseudonymizer=pseudonymizer,
        cipher=ValueCipher(b"e" * 32),
        ocr=NoOcr(),
        fields=repo.catalog,
    )
    result = run_pipeline(doc.tobytes(), Settings.from_env(), services)
    token = pseudonymizer.token("IBAN", "FR76 3000 6000 0112", "alphanumerique")
    assert result.status == "ok", result.reasons
    assert result.text.count(token) == 2
    assert "FR76" not in result.text
    assert result.report["champs"][-1] == "IBAN"
