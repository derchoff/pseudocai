"""Classification du type de document, sur le texte DÉJÀ pseudonymisé (avec les jetons)."""

from __future__ import annotations

import logging
import time

from ...catalog import CatalogError
from ...classification import STATUT_DESACTIVEE, STATUT_ERREUR, STATUT_NON_APPLICABLE, Classification, decide, unclassified
from ...config import Settings
from ...statistics.usage import agent_usage
from ..context import PipelineContext
from ..models import Services

log = logging.getLogger(__name__)


def classify(ctx: PipelineContext) -> None:
    """Classe le document ; une partie d'un ensemble (classify=False : pièce jointe, texte d'un mail,
    JSON) ne l'est pas, l'ensemble étant classé une seule fois par l'appelant."""
    step = time.monotonic()
    if ctx.source.classify:
        ctx.classification = classify_text(ctx.token_text, ctx.settings, ctx.services, ctx.consumption)
    else:
        ctx.classification = unclassified(STATUT_NON_APPLICABLE, ctx.settings.unclassified_folder)
    ctx.report["classification"] = ctx.classification.to_dict()
    if ctx.diag is not None and ctx.source.classify:
        ctx.diag["durees_s"]["classification"] = round(time.monotonic() - step, 2)


def classify_text(
    text: str, settings: Settings, services: Services, consumption: dict | None = None
) -> Classification:
    """Classe le texte pseudonymisé ; une erreur de l'agent range le document en « non classé ».

    Un référentiel invalide lève l'erreur, pour que le message soit réessayé. consumption : si donné,
    reçoit les appels et jetons de l'agent de classification (clé ia.classification).
    Sert aussi à la finalisation d'un mail, classé en entier.
    """
    if not settings.classification_enabled or services.classifier is None:
        return unclassified(STATUT_DESACTIVEE, settings.unclassified_folder)
    try:
        raw, doctypes, prompt_sha256 = services.classifier.classify(text)
    except CatalogError:
        raise  # référentiel invalide : erreur de configuration, le message sera réessayé
    except Exception:  # noqa: BLE001 - une classification ratée ne doit pas bloquer la pseudonymisation
        log.exception("Classification en échec : document rangé dans « %s »", settings.unclassified_folder)
        return unclassified(STATUT_ERREUR, settings.unclassified_folder, services.classifier.prompt_sha256)
    finally:
        if consumption is not None:
            consumption.setdefault("ia", {})["classification"] = agent_usage(services.classifier)
    return decide(
        raw,
        doctypes,
        min_confidence=settings.classification_min_confidence,
        secondary_min_confidence=settings.classification_secondary_min_confidence,
        unclassified_folder=settings.unclassified_folder,
        prompt_sha256=prompt_sha256,
    )
