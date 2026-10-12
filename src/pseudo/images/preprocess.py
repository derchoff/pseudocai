"""Copie destinée à l'OCR : redressement géométrique + amélioration photométrique.

La sortie conserve le cadrage d'origine (choix fonctionnel : les sorties restent réutilisables
comme jeux de test avec leurs défauts). Le redressement ne sert donc qu'à l'OCR ; la matrice
« original -> copie » permet de reporter ensuite les positions des mots sur l'original.

1. Perspective : contour du document (plus grand quadrilatère convexe couvrant au moins 25 % de
   l'image) ramené à un rectangle. Ignorée si aucun contour fiable n'est trouvé (scan à plat,
   document qui remplit le cadre).
2. Inclinaison : angle qui maximise la netteté des lignes de texte (profil de projection),
   recherché entre -10° et +10°.
3. Amélioration : niveaux de gris, débruitage, contraste local (CLAHE).
"""

from __future__ import annotations

import cv2
import numpy as np

DETECTION_LONG_SIDE = 1000
MIN_DOCUMENT_AREA = 0.25
MAX_SKEW_DEGREES = 10.0
SKEW_STEP_DEGREES = 0.5
MIN_SKEW_DEGREES = 0.3


def prepare_ocr_copy(image, *, enhance: bool = True):
    """Retourne (copie PIL en niveaux de gris, matrice 3×3 original -> copie, infos)."""
    from PIL import Image

    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    matrix = np.eye(3)
    info = {"perspective_corrigee": False, "inclinaison_degres": 0.0}

    quad = find_document_quad(gray)
    if quad is not None:
        warp, size = _perspective_matrix(quad)
        gray = cv2.warpPerspective(gray, warp, size, flags=cv2.INTER_CUBIC, borderValue=255)
        matrix = warp @ matrix
        info["perspective_corrigee"] = True

    angle = estimate_skew(gray)
    if abs(angle) >= MIN_SKEW_DEGREES:
        rotation, size = _rotation_matrix(gray.shape, angle)
        gray = cv2.warpPerspective(gray, rotation, size, flags=cv2.INTER_CUBIC, borderValue=255)
        matrix = rotation @ matrix
        info["inclinaison_degres"] = round(angle, 2)

    if enhance:
        gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    return Image.fromarray(gray), matrix.tolist(), info


def find_document_quad(gray: np.ndarray) -> np.ndarray | None:
    """Coins (haut-gauche, haut-droit, bas-droit, bas-gauche) du document, ou None."""
    factor = min(1.0, DETECTION_LONG_SIDE / max(gray.shape))
    small = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA) if factor < 1 else gray
    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if cv2.contourArea(approx) < MIN_DOCUMENT_AREA * image_area:
            break
        corners = _order_corners(approx.reshape(4, 2).astype(np.float64) / factor)
        if _touches_border(corners, gray.shape):
            return None  # le document remplit le cadre : rien à redresser
        return corners
    return None


def estimate_skew(gray: np.ndarray) -> float:
    """Angle (degrés) à appliquer pour remettre les lignes de texte à l'horizontale."""
    factor = min(1.0, DETECTION_LONG_SIDE / max(gray.shape))
    small = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA) if factor < 1 else gray
    _, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape
    center = (w / 2, h / 2)
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 1e-9, SKEW_STEP_DEGREES):
        rotated = cv2.warpAffine(binary, cv2.getRotationMatrix2D(center, angle, 1.0), (w, h))
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Range 4 coins dans l'ordre haut-gauche, haut-droit, bas-droit, bas-gauche."""
    s = points.sum(axis=1)
    d = np.diff(points, axis=1).ravel()
    return np.array([points[np.argmin(s)], points[np.argmin(d)], points[np.argmax(s)], points[np.argmax(d)]])


def _touches_border(corners: np.ndarray, shape, tolerance: float = 0.01) -> bool:
    """Vrai si tous les coins sont au bord de l'image (le document remplit déjà le cadre)."""
    h, w = shape
    margin_x, margin_y = w * tolerance, h * tolerance
    return all(
        x <= margin_x or x >= w - margin_x or y <= margin_y or y >= h - margin_y for x, y in corners
    )


def _perspective_matrix(corners: np.ndarray):
    """Matrice qui redresse le quadrilatère du document en rectangle, et taille de ce rectangle."""
    tl, tr, br, bl = corners
    width = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    height = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    warp = cv2.getPerspectiveTransform(corners.astype(np.float32), target)
    return warp.astype(np.float64), (width, height)


def _rotation_matrix(shape, angle: float):
    """Rotation autour du centre, avec agrandissement du canevas pour ne rien rogner."""
    h, w = shape
    affine = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(affine[0, 0]), abs(affine[0, 1])
    new_w, new_h = int(h * sin + w * cos), int(h * cos + w * sin)
    affine[0, 2] += new_w / 2 - w / 2
    affine[1, 2] += new_h / 2 - h / 2
    return np.vstack([affine, [0.0, 0.0, 1.0]]), (new_w, new_h)
