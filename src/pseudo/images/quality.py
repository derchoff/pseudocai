"""Contrôles qualité d'une image avant OCR : une image trop petite ou floue part en quarantaine
plutôt que de risquer un OCR raté (un nom mal lu est un nom non masqué)."""

from __future__ import annotations

import cv2
import numpy as np

ANALYSIS_LONG_SIDE = 1600  # le flou est mesuré à une résolution commune, pour des seuils stables


def blur_score(image) -> float:
    """Variance du laplacien (plus elle est faible, plus l'image est floue)."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    factor = ANALYSIS_LONG_SIDE / max(gray.shape)
    if factor < 1:
        gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def check_quality(image, *, min_short_side: int, blur_threshold: float) -> tuple[list[str], dict]:
    """Contrôle la résolution et la netteté d'une image.

    Renvoie (motifs de quarantaine, mesures pour le rapport).
    """
    width, height = image.size
    score = blur_score(image)
    reasons = []
    if min(width, height) < min_short_side:
        reasons.append("image_resolution_insuffisante")
    if score < blur_threshold:
        reasons.append("image_floue")
    return reasons, {"largeur": width, "hauteur": height, "score_nettete": round(score, 1)}
