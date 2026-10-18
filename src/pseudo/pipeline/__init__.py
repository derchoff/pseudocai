"""Pipeline de pseudonymisation : un document (PDF, image préparée) ou un texte déjà extrait
(texte d'un mail, JSON de métadonnées) entre, un résultat sort. Il ne touche à aucun stockage ni
à aucune file : les handlers (handler.py, mails/, json_jobs.py, cli.py) lisent le document,
appellent run_pipeline puis publient ou mettent en quarantaine.

- models.py : contrats des agents (Protocol), services injectés, entrée et résultat ;
- diagnostic.py : rapport détaillé (REPORT_DETAIL=diagnostic) ;
- runner.py : run_pipeline, qui sera découpé en étapes dans ce package.

Ce module expose l'API utilisée par le reste du code, qui ne change pas :
`from pseudo.pipeline import run_pipeline`.
"""

from .models import Classifier, Detector, PipelineInput, PipelineResult, Renderer, Services, SpanDetector, Verifier
from .runner import classify_text, run_pipeline

__all__ = [
    "Classifier",
    "Detector",
    "PipelineInput",
    "PipelineResult",
    "Renderer",
    "Services",
    "SpanDetector",
    "Verifier",
    "classify_text",
    "run_pipeline",
]
