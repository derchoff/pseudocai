"""Agents IA (Azure OpenAI) : détection, contrôle qualité et classification du document."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Callable, Iterable

from ..doctypes import UNCLASSIFIED, DocTypeCatalog
from ..models import Entity, Line
from ..prompts import RenderedPrompt

log = logging.getLogger(__name__)

def entities_schema(types: tuple[str, ...]) -> dict:
    """Schéma JSON strict imposé au modèle ; la liste des types vient du référentiel Markdown."""
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line_id": {"type": "string"},
                        "text": {"type": "string"},
                        "type": {"type": "string", "enum": list(types)},
                        "group": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["line_id", "text", "type", "group", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entities"],
        "additionalProperties": False,
    }


def classification_schema(doc_types: tuple[str, ...]) -> dict:
    """Schéma JSON strict de l'agent de classification (types du référentiel + NON_CLASSE)."""
    return {
        "type": "object",
        "properties": {
            "types": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": [*doc_types, UNCLASSIFIED]},
                        "confidence": {"type": "number"},
                        "indices": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type", "confidence", "indices"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["types"],
        "additionalProperties": False,
    }


class LlmError(RuntimeError):
    """Réponse inexploitable du modèle (refus, réponse tronquée)."""
    pass


class AzureOpenAIJsonAgent:
    """Appel Chat Completions en Structured Outputs (schéma JSON strict)."""

    def __init__(
        self,
        client,
        deployment: str,
        *,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        max_completion_tokens: int = 16000,
        max_concurrent_calls: int = 2,
        rate_limit_max_wait: float = 300,
    ):
        """client : client Azure OpenAI ; deployment : nom du déploiement du modèle.

        max_concurrent_calls : appels simultanés au modèle depuis cette instance (évite les rafales) ;
        rate_limit_max_wait : temps maximal, en secondes, passé à attendre après des refus 429.
        """
        self._client = client
        self._deployment = deployment
        self._reasoning_effort = reasoning_effort
        self._temperature = temperature
        self._max_tokens = max_completion_tokens
        self._slots = threading.BoundedSemaphore(max(1, max_concurrent_calls))
        self._max_wait = rate_limit_max_wait

    def run(self, system_prompt: str, user_content: str, schema: dict, schema_name: str = "entities") -> dict:
        """Appelle le modèle avec un schéma JSON strict (Structured Outputs) et renvoie la réponse décodée."""
        return self.run_with_usage(system_prompt, user_content, schema, schema_name)[0]

    def run_with_usage(
        self, system_prompt: str, user_content: str, schema: dict, schema_name: str = "entities"
    ) -> tuple[dict, dict]:
        """Comme run, et renvoie aussi la consommation de l'appel (jetons, durée, attente 429)."""
        kwargs: dict = {
            "model": self._deployment,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "max_completion_tokens": self._max_tokens,
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        started = time.monotonic()
        response, waited = self._create(kwargs)
        usage = _usage(response, time.monotonic() - started, waited)
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LlmError("Le modèle a refusé la requête")
        if choice.finish_reason == "length":
            raise LlmError("Réponse tronquée (max_completion_tokens) : réduire LLM_CHUNK_CHARS")
        return json.loads(choice.message.content or "{}"), usage


    def _create(self, kwargs: dict):
        """Appel au modèle, en attendant son tour quand Azure refuse pour dépassement de débit (429).

        Sans cette attente, le refus ferait échouer le traitement : Service Bus le relancerait
        aussitôt, refaisant tous les appels déjà réussis, jusqu'à la dead-letter.
        """
        waited = 0.0
        attempt = 0
        while True:
            try:
                with self._slots:
                    return self._client.chat.completions.create(**kwargs), waited
            except Exception as exc:  # noqa: BLE001 - seul le 429 est retenu, le reste remonte
                if getattr(exc, "status_code", None) != 429:
                    raise
                wait = _retry_delay(exc, attempt)
                if waited + wait > self._max_wait:
                    log.error("Débit Azure OpenAI dépassé depuis plus de %.0f s : abandon", self._max_wait)
                    raise
                log.warning("Débit Azure OpenAI dépassé (429) : nouvel essai dans %.1f s", wait)
                time.sleep(wait)
                waited += wait
                attempt += 1


def _usage(response, seconds: float, waited: float) -> dict:
    """Consommation d'un appel : jetons d'entrée, de sortie et de raisonnement, durée, attente 429."""
    u = getattr(response, "usage", None)
    details = getattr(u, "completion_tokens_details", None)
    return {
        "jetons_entree": getattr(u, "prompt_tokens", None) or 0,
        "jetons_sortie": getattr(u, "completion_tokens", None) or 0,
        "jetons_raisonnement": getattr(details, "reasoning_tokens", None) or 0,
        "duree_s": seconds,
        "attente_debit_s": waited,
    }


def _add_usage(total: dict, usage: dict) -> None:
    """Ajoute la consommation d'un appel au total."""
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value


def _retry_delay(exc, attempt: int) -> float:
    """Délai avant un nouvel essai : celui indiqué par Azure (retry-after), sinon croissant ; avec
    un peu de hasard, pour que les traitements en attente ne repartent pas tous en même temps."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        if headers.get("retry-after-ms"):
            base = float(headers["retry-after-ms"]) / 1000
        elif headers.get("retry-after"):
            base = float(headers["retry-after"])
        else:
            base = min(60.0, 5.0 * 2 ** attempt)
    except (TypeError, ValueError):
        base = min(60.0, 5.0 * 2 ** attempt)
    return max(1.0, base) + random.uniform(0, 3)


def format_lines(lines: Iterable[tuple[str, str]]) -> str:
    """Met les lignes au format attendu par les prompts : `<document>` puis `id: texte` par ligne."""
    body = "\n".join(f"{line_id}: {text}" for line_id, text in lines)
    return f"<document>\n{body}\n</document>"


def chunk_lines(lines: list[tuple[str, str]], max_chars: int, overlap: int) -> list[list[tuple[str, str]]]:
    """Découpe en blocs d'au plus max_chars, avec quelques lignes de recouvrement."""
    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    size = 0
    for item in lines:
        item_size = len(item[0]) + len(item[1]) + 3
        if current and size + item_size > max_chars:
            chunks.append(current)
            current = current[-overlap:] if overlap > 0 else []
            size = sum(len(a) + len(b) + 3 for a, b in current)
        current.append(item)
        size += item_size
    if current:
        chunks.append(current)
    return chunks


class EntityAgent:
    """Agent générique : envoie des lignes (id, texte) et récupère des entités."""

    def __init__(
        self,
        llm: AzureOpenAIJsonAgent,
        prompt_provider: Callable[[], RenderedPrompt],
        *,
        source: str,
        chunk_chars: int,
        overlap_lines: int,
        min_confidence: float,
    ):
        """source : nom de l'agent (llm, verifier) ; les entités sous min_confidence sont ignorées."""
        self._llm = llm
        self._prompt_provider = prompt_provider
        self._source = source
        self._chunk_chars = chunk_chars
        self._overlap = overlap_lines
        self._min_confidence = min_confidence
        self.last_prompt_sha256: str | None = None
        self._local = threading.local()  # statistiques du dernier appel, propres à chaque traitement

    @property
    def last_stats(self) -> dict | None:
        """Statistiques du dernier run de ce thread : appels, entités rendues ou écartées, jetons, durée."""
        return getattr(self._local, "stats", None)

    def run(self, lines: list[tuple[str, str]]) -> list[Entity]:
        """Fait analyser les lignes par blocs et renvoie les entités valides.

        Les types hors référentiel et les identifiants de ligne inconnus sont écartés.
        """
        stats: dict = {"appels": 0, "entites_rendues": 0, "ecartees_confiance": 0, "ligne_inconnue": 0}
        self._local.stats = stats  # remis à zéro à chaque appel, même sans ligne à analyser
        if not lines:
            return []
        prompt = self._prompt_provider()
        self.last_prompt_sha256 = prompt.sha256
        allowed_types = prompt.catalog.types
        schema = entities_schema(allowed_types)
        known_ids = {line_id for line_id, _ in lines}
        entities: list[Entity] = []
        for n, chunk in enumerate(chunk_lines(lines, self._chunk_chars, self._overlap)):
            data, usage = self._llm.run_with_usage(prompt.text, format_lines(chunk), schema)
            stats["appels"] += 1
            _add_usage(stats, usage)
            for raw in data.get("entities", []):
                stats["entites_rendues"] += 1
                text = (raw.get("text") or "").strip()
                etype = raw.get("type")
                if not text:
                    continue
                if etype not in allowed_types:
                    # Impossible avec un schéma strict ; journalisé si cela se produisait malgré tout.
                    log.warning("Type inconnu renvoyé par l'agent %s : %s", self._source, etype)
                    continue
                confidence = float(raw.get("confidence", 1.0))
                if confidence < self._min_confidence:
                    stats["ecartees_confiance"] += 1
                    continue
                line_id = raw.get("line_id", "")
                if line_id not in known_ids:
                    stats["ligne_inconnue"] += 1
                group = raw.get("group") or ""
                entities.append(
                    Entity(
                        line_id=line_id if line_id in known_ids else "",
                        text=text,
                        type=etype,
                        group=f"c{n}-{group}" if group else "",
                        confidence=confidence,
                        source=self._source,
                    )
                )
        for key in ("duree_s", "attente_debit_s"):
            stats[key] = round(stats.get(key, 0.0), 2)
        log.info("Agent %s : %d entité(s) sur %d ligne(s)", self._source, len(entities), len(lines))
        return entities


class DetectionAgent:
    """Agent de détection : repère les données personnelles dans le texte d'origine."""

    def __init__(self, agent: EntityAgent):
        """Enveloppe un EntityAgent configuré avec le prompt de détection."""
        self._agent = agent

    @property
    def prompt_sha256(self) -> str | None:
        """Empreinte du dernier prompt utilisé (tracée dans le rapport)."""
        return self._agent.last_prompt_sha256

    def detect(self, lines: list[Line]) -> list[Entity]:
        """Entités trouvées dans les lignes non vides du document."""
        return self._agent.run([(line.id, line.text) for line in lines if line.text.strip()])

    @property
    def last_stats(self) -> dict | None:
        """Statistiques du dernier appel (voir EntityAgent.last_stats)."""
        return self._agent.last_stats


class VerificationAgent:
    """Agent de vérification : cherche les données restées en clair dans le texte pseudonymisé."""

    def __init__(self, agent: EntityAgent):
        """Enveloppe un EntityAgent configuré avec le prompt de vérification."""
        self._agent = agent

    @property
    def prompt_sha256(self) -> str | None:
        """Empreinte du dernier prompt utilisé (tracée dans le rapport)."""
        return self._agent.last_prompt_sha256

    def verify(self, pseudonymised_lines: list[tuple[str, str]]) -> list[Entity]:
        """Résidus trouvés dans les lignes pseudonymisées (id, texte) non vides."""
        return self._agent.run([item for item in pseudonymised_lines if item[1].strip()])

    @property
    def last_stats(self) -> dict | None:
        """Statistiques du dernier appel (voir EntityAgent.last_stats)."""
        return self._agent.last_stats


class ClassificationAgent:
    """Classe le document à partir de son texte DÉJÀ PSEUDONYMISÉ (minimisation des données)."""

    def __init__(self, llm: AzureOpenAIJsonAgent, prompt_provider: Callable[[], RenderedPrompt], *, max_chars: int):
        """max_chars : nombre de caractères du texte envoyés au modèle."""
        self._llm = llm
        self._prompt_provider = prompt_provider
        self._max_chars = max_chars
        self.prompt_sha256: str | None = None
        self._local = threading.local()  # consommation du dernier appel, propre à chaque traitement

    @property
    def last_stats(self) -> dict | None:
        """Appels et jetons du dernier classement de ce thread."""
        return getattr(self._local, "stats", None)

    def classify(self, pseudonymised_text: str) -> tuple[list[dict], DocTypeCatalog, str]:
        """Demande au modèle les types probables du document pseudonymisé.

        Renvoie (types bruts avec confiances, référentiel utilisé, empreinte du prompt).
        """
        prompt = self._prompt_provider()
        self.prompt_sha256 = prompt.sha256
        excerpt = pseudonymised_text[: self._max_chars]
        self._local.stats = {"appels": 0}
        args = (prompt.text, f"<document>\n{excerpt}\n</document>", classification_schema(prompt.doctypes.types), "classification")
        if hasattr(self._llm, "run_with_usage"):
            data, usage = self._llm.run_with_usage(*args)
        else:
            data, usage = self._llm.run(*args), {}
        self._local.stats = {"appels": 1, **usage}
        raw = data.get("types", [])
        log.info("Classification : %s", [(t.get("type"), t.get("confidence")) for t in raw])
        return raw, prompt.doctypes, prompt.sha256
