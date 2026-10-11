"""Décision de classification à partir des scores renvoyés par l'agent IA.

L'agent renvoie une liste de types, chacun avec une confiance INDÉPENDANTE (0 à 1, les scores
ne totalisent pas forcément 1 : un document peut relever de deux types à la fois).

- type principal : le score le plus élevé, s'il atteint le seuil principal ; sinon NON_CLASSE.
  C'est lui qui détermine le sous-dossier de sortie.
- types conservés : ceux qui atteignent le seuil secondaire (plus le principal).
- multi_type : au moins deux types atteignent le seuil principal (document mixte).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .doctypes import UNCLASSIFIED, DocTypeCatalog

MAX_INDICES = 5
MAX_INDICE_CHARS = 80

# Statuts de la classification
STATUT_OK = "ok"  # un type principal a été retenu
STATUT_NON_CLASSE = "non_classe"  # l'agent a répondu, mais aucun type n'atteint le seuil
STATUT_ERREUR = "erreur"  # l'appel à l'agent a échoué
STATUT_DESACTIVEE = "desactivee"  # CLASSIFICATION_ENABLED=false
STATUT_NON_APPLICABLE = "non_applicable"  # document rejeté avant extraction (illisible, chiffré...)


@dataclass
class TypeScore:
    type: str
    confidence: float
    indices: list[str] = field(default_factory=list)


@dataclass
class Classification:
    principal: str
    dossier: str  # "" = pas de sous-dossier (classification désactivée)
    statut: str
    multi_type: bool = False
    seuil: float | None = None
    seuil_secondaire: float | None = None
    types: list[TypeScore] = field(default_factory=list)
    prompt_sha256: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def metadata(self) -> dict[str, str]:
        """Version courte pour les métadonnées de blob (ASCII uniquement)."""
        return {
            "document_type": self.dossier or "aucun",
            "document_types": ";".join(f"{default_ascii(t.type)}:{t.confidence:.2f}" for t in self.types),
            "classification_statut": self.statut,
        }


def default_ascii(value: str) -> str:
    return value.encode("ascii", "ignore").decode("ascii")


def unclassified(statut: str, unclassified_folder: str, prompt_sha256: str | None = None) -> Classification:
    folder = "" if statut == STATUT_DESACTIVEE else unclassified_folder
    return Classification(principal=UNCLASSIFIED, dossier=folder, statut=statut, prompt_sha256=prompt_sha256)


def decide(
    raw_types: list[dict],
    catalog: DocTypeCatalog,
    *,
    min_confidence: float,
    secondary_min_confidence: float,
    unclassified_folder: str,
    prompt_sha256: str | None = None,
) -> Classification:
    allowed = set(catalog.types)
    best: dict[str, TypeScore] = {}
    unclassified_score = 0.0
    for raw in raw_types:
        doc_type = raw.get("type")
        try:
            confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            continue
        if doc_type == UNCLASSIFIED:
            unclassified_score = max(unclassified_score, confidence)
            continue
        if doc_type not in allowed:
            continue
        indices = [str(i)[:MAX_INDICE_CHARS] for i in (raw.get("indices") or []) if str(i).strip()][:MAX_INDICES]
        if doc_type not in best or confidence > best[doc_type].confidence:
            best[doc_type] = TypeScore(doc_type, round(confidence, 3), indices)

    ranked = sorted(best.values(), key=lambda t: t.confidence, reverse=True)
    top = ranked[0] if ranked else None
    principal_ok = top is not None and top.confidence >= min_confidence and top.confidence >= unclassified_score
    kept = [t for t in ranked if t.confidence >= secondary_min_confidence or (principal_ok and t is top)]
    common = dict(
        seuil=min_confidence,
        seuil_secondaire=secondary_min_confidence,
        types=kept,
        prompt_sha256=prompt_sha256,
        multi_type=sum(1 for t in ranked if t.confidence >= min_confidence) >= 2,
    )
    if not principal_ok:
        return Classification(principal=UNCLASSIFIED, dossier=unclassified_folder, statut=STATUT_NON_CLASSE, **common)
    return Classification(principal=top.type, dossier=catalog.folder(top.type), statut=STATUT_OK, **common)
