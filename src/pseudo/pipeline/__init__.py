"""Pipeline de pseudonymisation : un document (PDF, image préparée) ou un texte déjà extrait
(texte d'un mail, JSON de métadonnées) entre, un résultat sort. Il ne touche à aucun stockage ni
à aucune file : les handlers (handler.py, mails/, json_jobs.py, cli.py) lisent le document,
appellent run_pipeline puis publient ou mettent en quarantaine.

- models.py : contrats des agents (Protocol), services injectés, entrée et résultat ;
- context.py : PipelineContext (état d'un document), Step (contrat d'une étape), Rejected (arrêt) ;
- runner.py : run_pipeline, qui enchaîne les étapes ;
- steps/ : une étape par fichier (lecture, détection, vérification, bilan, classification,
  valeurs fictives, rendu) ;
- diagnostic.py : rapport détaillé (REPORT_DETAIL=diagnostic).

Ce module expose l'API utilisée par le reste du code, qui ne change pas :
`from pseudo.pipeline import run_pipeline`.
"""

from .context import PipelineContext, Rejected, Step
from .models import Classifier, Detector, PipelineInput, PipelineResult, Renderer, Services, SpanDetector, Verifier
from .runner import run_pipeline, steps_for
from .steps import classify_text

__all__ = [
    "Classifier",
    "Detector",
    "PipelineContext",
    "PipelineInput",
    "PipelineResult",
    "Rejected",
    "Renderer",
    "Services",
    "SpanDetector",
    "Step",
    "Verifier",
    "classify_text",
    "run_pipeline",
    "steps_for",
]
