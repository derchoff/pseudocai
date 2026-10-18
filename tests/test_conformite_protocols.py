"""Conformité des implémentations aux contrats (Protocol) : Python ne les vérifie pas à l'exécution.

Les autres tests utilisent des doublures (FakeDetector, TruthOcr…) : une VRAIE classe Azure qui
s'écarterait de son contrat (méthode renommée, attribut absent, paramètre en trop) ne serait vue
qu'une fois déployée. Ce test construit les vraies classes avec des clients factices (aucun appel
Azure) et vérifie, pour chaque contrat :
- la présence de chaque méthode et de chaque attribut déclarés ;
- la compatibilité de chaque méthode : elle accepte les mêmes arguments positionnels, et ses
  paramètres en plus ont une valeur par défaut.
Les doublures des tests sont vérifiées aussi : elles doivent rester fidèles au contrat qu'elles imitent.
"""

import inspect
from types import SimpleNamespace

import pytest

from fakes import MemoryStore
from pseudo.config import Settings
from pseudo.detection.language_pii import LanguagePiiDetector
from pseudo.detection.llm import AzureOpenAIJsonAgent, ClassificationAgent, DetectionAgent, EntityAgent, VerificationAgent
from pseudo.extraction import DocumentIntelligenceOcr, OcrEngine
from pseudo.mapping_store import CosmosMappingStore, JsonFileMappingStore, MappingStore, NullMappingStore
from pseudo.pipeline import DOCUMENT, PART, TEXT, Classifier, Detector, SpanDetector, Step, Verifier
from pseudo.prepared_image import ImageRenderer, TransformedOcr
from pseudo.redaction import render_pdf
from test_classification import FakeClassifier
from test_pipeline import FakeDetector, FakeVerifier, TruthOcr, make_text_pdf


# --------------------------------------------------------------------------- outils de vérification


def contract(protocol) -> tuple[set[str], dict[str, inspect.Signature]]:
    """Attributs et méthodes déclarés par un Protocol (sans ceux hérités de Protocol lui-même)."""
    attributes = set(protocol.__dict__.get("__annotations__", {}))
    methods = {
        name: inspect.signature(member)
        for name, member in protocol.__dict__.items()
        if callable(member) and (not name.startswith("_") or name == "__call__")
    }
    return attributes, methods


def positional(signature: inspect.Signature, drop_self: bool) -> list[inspect.Parameter]:
    """Paramètres positionnels d'une signature (sans self pour une méthode non liée)."""
    params = list(signature.parameters.values())
    if drop_self and params and params[0].name == "self":
        params = params[1:]
    return [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]


def compatible(expected: inspect.Signature, actual: inspect.Signature) -> str | None:
    """None si `actual` (méthode liée, ou fonction) accepte les appels prévus par `expected`
    (méthode du Protocol, avec self) ; sinon la raison."""
    wanted = positional(expected, drop_self=True)
    params = list(actual.parameters.values())
    accepted = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
    if len(accepted) < len(wanted) and not varargs:
        return f"attend {len(wanted)} argument(s), n'en accepte que {len(accepted)} : {actual}"
    extra = [p for p in accepted[len(wanted):] if p.default is p.empty]
    required_keywords = [p for p in params if p.kind == p.KEYWORD_ONLY and p.default is p.empty]
    if extra or required_keywords:
        names = ", ".join(p.name for p in extra + required_keywords)
        return f"paramètre(s) obligatoire(s) en plus du contrat : {names} : {actual}"
    return None


def violations(obj, protocol) -> list[str]:
    """Écarts entre un objet et un Protocol : membres absents, méthodes incompatibles."""
    attributes, methods = contract(protocol)
    problems = [f"attribut « {name} » absent" for name in sorted(attributes) if not hasattr(obj, name)]
    for name, expected in sorted(methods.items()):
        member = obj if name == "__call__" else getattr(obj, name, None)
        if member is None or not callable(member):
            problems.append(f"méthode « {name} » absente")
            continue
        reason = compatible(expected, inspect.signature(member))
        if reason:
            problems.append(f"méthode « {name} » : {reason}")
    return problems


# --------------------------------------------------------------------------- vraies classes


@pytest.fixture(scope="module")
def llm():
    """Client Azure OpenAI factice : aucune requête n'est envoyée."""
    return AzureOpenAIJsonAgent(SimpleNamespace(), "deploiement-factice")


def entity_agent(llm, source):
    """Agent générique tel que le construit runtime.build_runtime."""
    return EntityAgent(llm, lambda: None, source=source, chunk_chars=12000, overlap_lines=3, min_confidence=0.3)


def real_implementations(llm, tmp_path):
    """(contrat, nom, objet) pour chaque vraie implémentation utilisée en production."""
    database = SimpleNamespace(get_container_client=lambda name: SimpleNamespace(name=name))
    return [
        (Detector, "DetectionAgent", DetectionAgent(entity_agent(llm, "llm"))),
        (Verifier, "VerificationAgent", VerificationAgent(entity_agent(llm, "verifier"))),
        (Classifier, "ClassificationAgent", ClassificationAgent(llm, lambda: None, max_chars=6000)),
        (SpanDetector, "LanguagePiiDetector", LanguagePiiDetector(SimpleNamespace(), {}, 0.5)),
        (OcrEngine, "DocumentIntelligenceOcr", DocumentIntelligenceOcr(SimpleNamespace())),
        (OcrEngine, "TransformedOcr", TransformedOcr(DocumentIntelligenceOcr(SimpleNamespace()), b"", [])),
        (MappingStore, "CosmosMappingStore", CosmosMappingStore(database, "m", "t", "o", "s")),
        (MappingStore, "NullMappingStore", NullMappingStore()),
        (MappingStore, "JsonFileMappingStore", JsonFileMappingStore(tmp_path / "correspondances.json")),
    ]


def test_real_classes_follow_their_contract(llm, tmp_path):
    problems = [
        f"{name} ({protocol.__name__}) : {p}"
        for protocol, name, obj in real_implementations(llm, tmp_path)
        for p in violations(obj, protocol)
    ]
    assert problems == []


def test_agents_expose_their_consumption(llm):
    """La consommation est lue avec getattr(agent, "last_stats") : un attribut renommé donnerait des
    zéros sans aucune erreur. Les vrais agents doivent donc l'exposer."""
    agents = {
        "DetectionAgent": DetectionAgent(entity_agent(llm, "llm")),
        "VerificationAgent": VerificationAgent(entity_agent(llm, "verifier")),
        "ClassificationAgent": ClassificationAgent(llm, lambda: None, max_chars=6000),
        "LanguagePiiDetector": LanguagePiiDetector(SimpleNamespace(), {}, 0.5),
    }
    missing = [name for name, agent in agents.items() if not hasattr(type(agent), "last_stats")]
    assert missing == []


def test_renderers_accept_the_renderer_call():
    """Renderer = Callable[[bytes, DocumentContent, list[Span], int], bytes] : 4 arguments positionnels."""
    image = ImageRenderer(b"", "jpeg", [], Settings.from_env())
    for name, renderer in (("render_pdf", render_pdf), ("ImageRenderer", image)):
        params = positional(inspect.signature(renderer), drop_self=False)
        assert len(params) >= 4 and all(p.default is not p.empty for p in params[4:]), name


# --------------------------------------------------------------------------- étapes du pipeline


@pytest.mark.parametrize("suite_name, suite", [("DOCUMENT", DOCUMENT), ("PART", PART), ("TEXT", TEXT)])
def test_pipeline_steps_follow_the_step_contract(suite_name, suite):
    problems = [f"{getattr(s, '__name__', s)} : {p}" for s in suite for p in violations(s, Step)]
    assert problems == []
    names = [s.name for s in suite]
    assert len(names) == len(set(names)), f"{suite_name} : deux étapes portent le même nom {names}"


# --------------------------------------------------------------------------- doublures des tests


def test_test_doubles_stay_faithful_to_the_contracts():
    doubles = [
        (Detector, "FakeDetector", FakeDetector({})),
        (Verifier, "FakeVerifier", FakeVerifier()),
        (Classifier, "FakeClassifier", FakeClassifier()),
        (OcrEngine, "TruthOcr", TruthOcr(make_text_pdf())),
        (MappingStore, "MemoryStore", MemoryStore()),
    ]
    problems = [f"{name} ({protocol.__name__}) : {p}" for protocol, name, obj in doubles for p in violations(obj, protocol)]
    assert problems == []


# --------------------------------------------------------------------------- l'outil lui-même


def test_the_check_catches_a_broken_implementation():
    class Renamed:
        prompt_sha256 = None

        def find(self, lines):
            return []

    class TooManyArguments:
        prompt_sha256 = None

        def detect(self, lines, threshold):
            return []

    class MissingAttribute:
        def detect(self, lines):
            return []

    assert violations(Renamed(), Detector) == ["méthode « detect » absente"]
    assert "threshold" in violations(TooManyArguments(), Detector)[0]
    assert violations(MissingAttribute(), Detector) == ["attribut « prompt_sha256 » absent"]

    def nameless(ctx):
        pass

    assert violations(nameless, Step) == ["attribut « name » absent"]
