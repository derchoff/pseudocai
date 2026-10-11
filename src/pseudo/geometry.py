"""Transformations projectives 3×3 (homographies), en Python pur.

Sert à reporter sur l'image d'origine les positions des mots lus dans la copie OCR redressée :
    copie OCR = M · original      =>      original = M⁻¹ · copie OCR
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

Matrix = list[list[float]]

IDENTITY: Matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def multiply(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def invert(m: Matrix) -> Matrix:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("Transformation non inversible")
    adj = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    return [[v / det for v in row] for row in adj]


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    xs = m[0][0] * x + m[0][1] * y + m[0][2]
    ys = m[1][0] * x + m[1][1] * y + m[1][2]
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    return xs / w, ys / w


def apply_quad(m: Matrix, quad: tuple[float, ...]) -> tuple[float, ...]:
    """Applique m aux 4 sommets (x1, y1, ..., x4, y4) d'un quadrilatère."""
    out: list[float] = []
    for k in range(0, 8, 2):
        out.extend(apply(m, quad[k], quad[k + 1]))
    return tuple(out)


def bbox(quad: tuple[float, ...]) -> tuple[float, float, float, float]:
    xs, ys = quad[0::2], quad[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def homography(src: tuple[float, ...], dst: tuple[float, ...]) -> Matrix:
    """Homographie qui envoie les 4 sommets de src sur ceux de dst (quadrilatères x1, y1, ..., x4, y4)."""
    rows: list[list[float]] = []
    for k in range(0, 8, 2):
        x, y, u, v = src[k], src[k + 1], dst[k], dst[k + 1]
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, v])
    # Élimination de Gauss avec pivot partiel (système 8 × 8).
    for col in range(8):
        pivot = max(range(col, 8), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise ValueError("Quadrilatère dégénéré")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(8):
            if r != col:
                f = rows[r][col] / rows[col][col]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[col])]
    h = [rows[i][8] / rows[i][i] for i in range(8)]
    return [h[0:3], h[3:6], [h[6], h[7], 1.0]]


def scale(factor: float) -> Matrix:
    return [[factor, 0.0, 0.0], [0.0, factor, 0.0], [0.0, 0.0, 1.0]]


@dataclass
class PageGeometry:
    """Géométrie d'une page d'image préparée (fichier transforms.json de work/<job_id>/)."""

    width: int  # pixels de l'image d'origine (après orientation EXIF)
    height: int
    dpi: int  # résolution utilisée pour emballer l'image en PDF (points = pixels × 72 / dpi)
    to_ocr: Matrix  # pixels de l'original -> pixels de la copie OCR


def dump_geometry(pages: list[PageGeometry]) -> bytes:
    return json.dumps({"pages": [asdict(p) for p in pages]}).encode("utf-8")


def load_geometry(data: bytes) -> list[PageGeometry]:
    return [PageGeometry(**page) for page in json.loads(data)["pages"]]
