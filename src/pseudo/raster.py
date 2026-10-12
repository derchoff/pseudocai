"""Remplacement dessiné dans les pixels : photos, images et pages scannées d'un PDF.

Les mots lus par l'OCR sont des quadrilatères : sur une photo prise de biais ou un scan penché,
ils suivent l'inclinaison et la perspective du texte. Pour chaque zone à remplacer :
- le fond de chaque mot est repeint en suivant son quadrilatère (couleur du fond environnant en
  mode substitution, gris en mode jetons) ;
- le remplacement est dessiné « à plat » dans une vignette transparente, puis déformé pour épouser
  le quadrilatère de la zone (même inclinaison, même perspective, même hauteur de texte).

Deux passes : TOUS les fonds d'abord, puis TOUS les textes. Sinon le fond d'une zone de la ligne
suivante, légèrement agrandi, mordrait sur le texte écrit juste avant.
"""

from __future__ import annotations

import io
from functools import lru_cache

import pymupdf

from .geometry import bbox, homography
from .models import Line, Span

FILL_RGB = (217, 217, 217)
TEXT_RGB = (0, 0, 0)
MARGIN_PX = 2.0
MAX_STRETCH = 1.5  # une valeur fictive plus longue peut s'allonger jusqu'à 1,5 fois la zone


def ring_color(pixel, x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> tuple[int, int, int]:
    """Couleur du fond autour d'une zone : couleur la PLUS FRÉQUENTE des pixels du pourtour. Le
    texte voisin ou un trait de cadre qui longe un côté sont ainsi ignorés. `pixel(x, y)` renvoie
    (r, g, b) en 0-255."""
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(width - 1, x1 + 2), min(height - 1, y1 + 2)
    step = max(1, (x1 - x0 + y1 - y0) // 200)
    points = [(x, y) for x in range(x0, x1 + 1, step) for y in (y0, y1)]
    points += [(x, y) for y in range(y0, y1 + 1, step) for x in (x0, x1)]
    if not points:
        return (255, 255, 255)
    samples = [pixel(x, y)[:3] for x, y in points]
    buckets: dict[tuple, list] = {}
    for c in samples:
        buckets.setdefault(tuple(v // 16 for v in c), []).append(c)
    dominant = max(buckets.values(), key=len)
    return tuple(round(sum(c[i] for c in dominant) / len(dominant)) for i in range(3))


def rect_quad(rect) -> tuple[float, ...]:
    """Quadrilatère (haut-gauche, haut-droit, bas-droit, bas-gauche) d'un rectangle."""
    x0, y0, x1, y1 = rect
    return (x0, y0, x1, y0, x1, y1, x0, y1)


def word_quad(word) -> tuple[float, ...]:
    """Quadrilatère du mot (celui de l'OCR, sinon son rectangle)."""
    return word.quad or rect_quad(word.rect)


def span_quads(line: Line, span: Span) -> list[tuple[float, ...]]:
    """Quadrilatères des mots couverts par la zone (un mot partiellement couvert est masqué en entier)."""
    return [word_quad(w) for w in line.words if w.start < span.end and w.end > span.start]


def paint_spans(frame, items: list[tuple[Line, Span]], px_per_pt: float) -> None:
    """Remplace les zones dans `frame` (image PIL modifiée sur place). Les positions des mots sont
    en points ; `px_per_pt` les convertit en pixels de l'image."""
    from PIL import ImageDraw

    def px(quad):
        """Convertit un quadrilatère de points en pixels."""
        return [v * px_per_pt for v in quad]

    # 0. Mesures sur l'image intacte : quadrilatères, couleur du fond, zone d'écriture.
    plans = []
    for line, span in items:
        quads = [px(q) for q in span_quads(line, span)]
        if not quads:
            continue
        x0, y0, x1, y1 = (int(v) for v in bbox(tuple(v for q in quads for v in q)))
        fill = FILL_RGB
        if span.surrogate is not None:
            fill = ring_color(lambda x, y: frame.getpixel((x, y)), x0, y0, x1, y1, frame.width, frame.height)
        # Zone d'écriture : bord gauche du premier mot, bord droit du dernier.
        first, last = quads[0], quads[-1]
        zone = (first[0], first[1], last[2], last[3], last[4], last[5], first[6], first[7])
        if span.surrogate is not None:
            zone = _extend(zone, [px(word_quad(w)) for w in line.words if w.start >= span.end])
        plans.append((span, quads, fill, zone))

    # 1. Tous les fonds.
    draw = ImageDraw.Draw(frame)
    for _, quads, fill, _ in plans:
        for quad in quads:
            draw.polygon(_expand(quad, MARGIN_PX), fill=fill)
    # 2. Tous les textes : un seul remplacement par zone, même si la valeur couvre plusieurs mots.
    for span, _, _, zone in plans:
        if span.surrogate is not None:
            _draw_in_quad(frame, zone, span.surrogate, _draw_surrogate)
        else:
            _draw_in_quad(frame, zone, span.token, _draw_token)


def _expand(px: list[float], margin: float) -> list[tuple[float, float]]:
    """Agrandit un quadrilatère de margin pixels autour de son centre."""
    cx = sum(px[0::2]) / 4
    cy = sum(px[1::2]) / 4
    points = []
    for x, y in zip(px[0::2], px[1::2]):
        dx, dy = x - cx, y - cy
        norm = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        points.append((x + dx / norm * margin, y + dy / norm * margin))
    return points


def _extend(quad: tuple[float, ...], following: list[list[float]], limit: float = MAX_STRETCH) -> tuple[float, ...]:
    """Allonge la zone dans le sens de la ligne pour qu'une valeur fictive plus longue garde la taille
    du texte d'origine : jusqu'au mot suivant de la ligne, au plus `limit` fois la longueur."""
    dx, dy = quad[2] - quad[0], quad[3] - quad[1]
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        return quad
    k = limit
    for w in following:
        t = ((w[0] - quad[0]) * dx + (w[1] - quad[1]) * dy) / length2  # position le long de la ligne
        if t > 1:
            k = min(k, t - 6 / length2 ** 0.5)  # petit espace avant le mot suivant
    k = max(k, 1.0)
    bx, by = quad[4] - quad[6], quad[5] - quad[7]
    return (quad[0], quad[1], quad[0] + dx * k, quad[1] + dy * k,
            quad[6] + bx * k, quad[7] + by * k, quad[6], quad[7])


def _draw_in_quad(frame, quad, text: str, draw_text) -> None:
    """Dessine le texte « à plat » dans une vignette transparente, puis la déforme pour qu'elle
    épouse le quadrilatère (TL, TR, BR, BL) de la zone. Seuls les pixels du texte sont posés :
    le reste de la vignette est transparent et ne recouvre rien."""
    from PIL import Image, ImageDraw

    def dist(a: int, b: int) -> float:
        """Distance entre deux sommets du quadrilatère."""
        return ((quad[a] - quad[b]) ** 2 + (quad[a + 1] - quad[b + 1]) ** 2) ** 0.5

    width = max(1, round(max(dist(0, 2), dist(6, 4))))
    height = max(1, round(max(dist(0, 6), dist(2, 4))))
    tile = Image.new("RGBA", (width, height), TEXT_RGB + (0,))
    draw_text(ImageDraw.Draw(tile), text, width, height)

    x0, y0, x1, y1 = bbox(tuple(quad))
    left, top = max(0, int(x0) - 1), max(0, int(y0) - 1)
    right, bottom = min(frame.width, int(x1) + 2), min(frame.height, int(y1) + 2)
    if right <= left or bottom <= top:
        return
    # PIL attend la transformation « pixel de la région -> pixel de la vignette ».
    local = tuple(v - (left if i % 2 == 0 else top) for i, v in enumerate(quad))
    m = homography(local, (0, 0, width, 0, width, height, 0, height))
    coeffs = (m[0][0], m[0][1], m[0][2], m[1][0], m[1][1], m[1][2], m[2][0], m[2][1])
    warped = tile.transform((right - left, bottom - top), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)
    region = frame.crop((left, top, right, bottom)).convert("RGBA")
    region.alpha_composite(warped)
    frame.paste(region.convert(frame.mode), (left, top))


def _fit(draw, text: str, size: int, width: float):
    """Police à la taille demandée, réduite si le texte dépasse la largeur."""
    font = _font(size)
    text_width = draw.textlength(text, font=font)
    if text_width > width > 0:
        font = _font(max(6, int(size * width / text_width)))
    return font


def _draw_token(draw, token: str, width: int, height: int) -> None:
    """Dessine le jeton centré dans la vignette."""
    font = _fit(draw, token, max(6, int(height * 0.6)), width * 0.95)
    draw.text((width / 2, height * 0.78), token, fill=TEXT_RGB + (255,), font=font, anchor="ms")


def _draw_surrogate(draw, text: str, width: int, height: int) -> None:
    """Valeur fictive : texte noir aligné à gauche, ligne de base et hauteur proches de l'original."""
    font = _fit(draw, text, max(6, int(height * 0.75)), width)
    draw.text((0, height * 0.78), text, fill=TEXT_RGB + (255,), font=font, anchor="ls")


@lru_cache(maxsize=1)
def _font_bytes() -> bytes:
    """Fichier de la police Helvetica fournie par PyMuPDF."""
    # Helvetica embarquée par PyMuPDF (la même que pour les PDF) : la police par défaut de Pillow
    # n'a pas les lettres accentuées.
    return pymupdf.Font("helv").buffer


def _font(size: int):
    """Police Helvetica à la taille demandée, pour Pillow."""
    from PIL import ImageFont

    return ImageFont.truetype(io.BytesIO(_font_bytes()), size)
