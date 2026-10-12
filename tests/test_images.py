"""Support des images : formats, routage, décodage/encodage, qualité, redressement, chaîne complète."""

import io
import json

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageFilter

from fakes import FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.extraction import extract_native_page
from pseudo.formats import BMP, HEIF, JPEG, PDF, PNG, TIFF, WEBP, format_from_name
from pseudo.geometry import apply, invert, load_geometry, multiply
from pseudo.image_codec import EXIF_ORIENTATION, decode, encode
from pseudo.images.prepare import handle_image_job, prepare_image
from pseudo.images.preprocess import estimate_skew, find_document_quad
from pseudo.images.quality import blur_score, check_quality
from pseudo.models import Line, PageContent, Word
from pseudo.router import QUEUE_IMAGE, QUEUE_PDF, route_event
from pseudo.runtime import Runtime
from test_pipeline import FakeDetector, make_text_pdf, services

DOC_DPI = 150
# Coins du document dans la « photo » : prise de vue légèrement en biais.
PHOTO_SIZE = (1500, 2000)
PHOTO_QUAD = [(150, 120), (1350, 180), (1300, 1880), (120, 1820)]
FILL = 217
MAKE, MODEL = 0x010F, 0x0110


@pytest.fixture
def settings(monkeypatch):
    """Réglages avec le traitement des images activé."""
    for key in ("IMAGE_PROCESSING_ENABLED", "IMAGE_ENHANCE_FOR_OCR", "CLASSIFICATION_ENABLED", "OCR_NATIVE_PAGES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    return Settings.from_env()


def document_image() -> Image.Image:
    """Facture de test rendue en image (le document « à plat »)."""
    page = pymupdf.open(stream=make_text_pdf(), filetype="pdf")[0]
    pix = page.get_pixmap(dpi=DOC_DPI)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def doc_to_photo_matrix(doc: Image.Image) -> np.ndarray:
    """Matrice qui place le document à plat aux coins PHOTO_QUAD de la photo."""
    w, h = doc.size
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    return cv2.getPerspectiveTransform(src, np.float32(PHOTO_QUAD)).astype(np.float64)


def make_photo(blur: float = 0.0) -> tuple[bytes, np.ndarray]:
    """Photo JPEG d'une facture fictive posée sur une table sombre, vue en perspective."""
    doc = document_image()
    p = doc_to_photo_matrix(doc)
    warped = cv2.warpPerspective(np.asarray(doc), p, PHOTO_SIZE, borderValue=(45, 40, 38))
    img = Image.fromarray(warped)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue(), p


def truth_words_px() -> list[tuple[str, list[Word]]]:
    """Mots de la facture (vérité terrain) en pixels du document rendu à DOC_DPI."""
    page = pymupdf.open(stream=make_text_pdf(), filetype="pdf")[0]
    k = DOC_DPI / 72
    out = []
    for line in extract_native_page(page).lines:
        out.append((line.id, [Word(w.text, tuple(v * k for v in w.rect)) for w in line.words]))
    return out


class PhotoOcr:
    """Simule Document Intelligence sur la copie redressée : renvoie les vrais mots, positionnés
    dans la copie OCR (photo P puis redressement M), en points de ocr.pdf."""

    def __init__(self, p: np.ndarray, geometry):
        """p : matrice document -> photo ; geometry : géométrie produite par la préparation."""
        self.p = p.tolist()
        self.geometry = geometry
        self.calls = 0

    def ocr(self, pdf_bytes, doc, page_indexes):
        """Vrais mots du document, positionnés dans la copie redressée (points de ocr.pdf)."""
        self.calls += 1
        g = self.geometry[0]
        to_ocr = multiply(g.to_ocr, self.p)
        k = 72 / g.dpi
        lines = []
        for n, (_, words) in enumerate(truth_words_px(), start=1):
            out = []
            for w in words:
                x0, y0, x1, y1 = w.rect
                quad = []
                for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                    qx, qy = apply(to_ocr, x, y)
                    quad += [qx * k, qy * k]
                xs, ys = quad[0::2], quad[1::2]
                out.append(Word(w.text, (min(xs), min(ys), max(xs), max(ys)), 0.97, quad=tuple(quad)))
            lines.append(Line.build(f"p1-l{n}", 0, out, source="ocr"))
        return [PageContent(index=0, source="ocr", lines=lines, ocr_confidence=0.97)]


# ---------------------------------------------------------------- formats et routage


@pytest.mark.parametrize(
    "name, fmt",
    [
        ("dossier/facture.pdf", PDF),
        ("FACTURE.PDF", PDF),
        ("photo.jpg", JPEG),
        ("photo.JPEG", JPEG),
        ("scan.png", PNG),
        ("scan.tif", TIFF),
        ("scan.tiff", TIFF),
        ("photo.webp", WEBP),
        ("scan.bmp", BMP),
        ("IMG_0001.HEIC", HEIF),
        ("photo.heif", HEIF),
        ("archive.zip", None),
        ("sans-extension", None),
    ],
)
def test_format_comes_from_extension_only(name, fmt):
    assert format_from_name(name) == fmt


def storage(settings, blobs=None, store=None, svc=None):
    """Runtime de test (stockage en mémoire)."""
    return Runtime(settings=settings, services=svc, mapping_store=store or MemoryStore(), blob_service=blobs or FakeBlobService())


def test_router_sends_pdf_and_images_to_their_queue(settings):
    ctx = storage(settings)
    ctx.blob_service.put("incoming", "a.pdf", make_text_pdf())
    ctx.blob_service.put("incoming", "photo.jpg", make_photo()[0])
    assert route_event(event("a.pdf"), ctx).queue == QUEUE_PDF
    image = route_event(event("photo.jpg"), ctx)
    assert (image.queue, image.job.origin, image.job.source_format) == (QUEUE_IMAGE, "image", "jpeg")


def test_image_renamed_as_pdf_goes_to_quarantine(settings):
    """Seule l'extension compte : une photo renommée en .pdf est traitée comme un PDF, sa lecture
    échoue et elle part en quarantaine (contenu non conforme : tentative de contournement possible)."""
    photo, _ = make_photo()
    runtime = image_runtime(settings, photo, name="lot1/photo-renommee.pdf")
    status, messages = run_chain(runtime, "lot1/photo-renommee.pdf")
    assert status == "quarantine" and json.loads(messages[0])["origin"] == "pdf"
    motif = json.loads(runtime.blob_service.data("quarantine", "non-classe/lot1/photo-renommee.pdf.motif.json"))
    assert motif["motifs"] == ["pdf_illisible"] and motif["etape"] == "pseudonymisation"
    assert runtime.blob_service.names("pseudonymised") == []


def test_image_with_wrong_extension_goes_to_quarantine(settings):
    """Un PNG renommé en .jpg n'est pas réinterprété : la lecture en JPEG échoue."""
    buffer = io.BytesIO()
    Image.open(io.BytesIO(make_photo()[0])).save(buffer, format="PNG")
    runtime = image_runtime(settings, buffer.getvalue(), name="lot1/scan.jpg")
    status, _ = run_chain(runtime, "lot1/scan.jpg", prepare_image=handle_image_job)
    assert status == "quarantine"
    motif = json.loads(runtime.blob_service.data("quarantine", "non-classe/lot1/scan.jpg.motif.json"))
    assert motif["motifs"] == ["image_illisible"] and motif["etape"] == "preparation_image"


def test_router_quarantines_unknown_formats_and_disabled_images(settings, monkeypatch):
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "false")
    ctx = storage(Settings.from_env())
    ctx.blob_service.put("incoming", "photo.jpg", make_photo()[0])
    ctx.blob_service.put("incoming", "archive.zip", b"PK\x03\x04")
    assert route_event(event("photo.jpg"), ctx).status == "quarantine"
    assert route_event(event("archive.zip"), ctx).status == "quarantine"
    motifs = {
        name: json.loads(ctx.blob_service.data("quarantine", name))["motifs"]
        for name in ctx.blob_service.names("quarantine")
        if name.endswith(".motif.json")
    }
    assert motifs == {
        "non-classe/photo.jpg.motif.json": ["traitement_images_desactive"],
        "non-classe/archive.zip.motif.json": ["format_non_supporte"],
    }
    assert {t["stage"] for t in ctx.mapping_store.traces} == {"routage"}


# ---------------------------------------------------------------- géométrie, décodage, encodage


def test_homography_inverse_round_trip():
    m = [[1.1, 0.05, 30.0], [-0.02, 0.95, 12.0], [0.0001, 0.00005, 1.0]]
    x, y = apply(m, 120.0, 340.0)
    assert apply(invert(m), x, y) == pytest.approx((120.0, 340.0))


def test_exif_orientation_is_applied_and_removed_from_output():
    upright = Image.new("RGB", (300, 200), "white")
    stored = upright.rotate(90, expand=True)  # pixels stockés « couchés »
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6  # « tourner de 90° pour afficher »
    exif[MAKE], exif[MODEL] = "FictivePhone", "FP-1"  # métadonnées de l'appareil
    buffer = io.BytesIO()
    stored.save(buffer, format="JPEG", exif=exif)
    decoded = decode(buffer.getvalue(), JPEG, 10_000_000)
    assert decoded.exif_orientation == 6 and decoded.frames[0].size == (300, 200)
    out = Image.open(io.BytesIO(encode(decoded.frames, JPEG)))
    assert out.format == "JPEG" and out.size == (300, 200)
    assert "exif" not in out.info and len(out.getexif()) == 0


def test_multipage_tiff_and_transparent_png():
    frames = [Image.new("RGB", (120, 80), c) for c in ("white", "gray")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    tiff = decode(buffer.getvalue(), TIFF, 10_000_000)
    assert [f.getpixel((0, 0)) for f in tiff.frames] == [(255, 255, 255), (128, 128, 128)]
    again = decode(encode(tiff.frames, TIFF), TIFF, 10_000_000)
    assert [f.getpixel((0, 0)) for f in again.frames] == [(255, 255, 255), (128, 128, 128)]
    tags = Image.open(io.BytesIO(encode(tiff.frames, TIFF))).getexif()
    assert not ({MAKE, MODEL, EXIF_ORIENTATION} & set(tags))

    rgba = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    png = decode(buffer.getvalue(), PNG, 10_000_000)
    assert png.frames[0].mode == "RGB" and png.frames[0].getpixel((0, 0)) == (255, 255, 255)


def test_heif_round_trip():
    pytest.importorskip("pillow_heif")
    buffer = io.BytesIO()
    try:
        Image.new("RGB", (64, 48), "white").save(buffer, format="HEIF")
    except (KeyError, OSError, ValueError):
        pytest.skip("encodeur HEIF indisponible")
    decoded = decode(buffer.getvalue(), HEIF, 10_000_000)
    assert decoded.frames[0].size == (64, 48)
    assert Image.open(io.BytesIO(encode(decoded.frames, HEIF))).format == "HEIF"


# ---------------------------------------------------------------- qualité et redressement


def test_quality_checks():
    sharp = Image.open(io.BytesIO(make_photo()[0]))
    blurred = Image.open(io.BytesIO(make_photo(blur=6)[0]))
    assert blur_score(sharp) > 60 > blur_score(blurred)
    assert check_quality(sharp, min_short_side=1000, blur_threshold=60)[0] == []
    assert check_quality(blurred, min_short_side=1000, blur_threshold=60)[0] == ["image_floue"]
    small = sharp.resize((600, 800))
    assert "image_resolution_insuffisante" in check_quality(small, min_short_side=1000, blur_threshold=0)[0]


def test_document_contour_is_found_on_the_photo():
    gray = cv2.cvtColor(np.asarray(Image.open(io.BytesIO(make_photo()[0]))), cv2.COLOR_RGB2GRAY)
    quad = find_document_quad(gray)
    assert quad is not None
    assert np.abs(quad - np.array(PHOTO_QUAD)).max() < 15


def test_skew_is_estimated():
    doc = np.asarray(document_image().convert("L"))
    h, w = doc.shape
    rotated = cv2.warpAffine(doc, cv2.getRotationMatrix2D((w / 2, h / 2), -4, 1.0), (w, h), borderValue=255)
    assert estimate_skew(rotated) == pytest.approx(4, abs=0.6)


def test_prepare_keeps_original_framing_for_rendering(settings):
    photo, _ = make_photo()
    artifacts = prepare_image(photo, JPEG, settings)
    assert artifacts.reasons == []
    render = pymupdf.open(stream=artifacts.render_pdf, filetype="pdf")[0]
    # Page = photo d'origine, cadrage conservé (points = pixels × 72 / dpi).
    assert (round(render.rect.width * settings.image_render_dpi / 72), round(render.rect.height * settings.image_render_dpi / 72)) == PHOTO_SIZE
    geometry = load_geometry(artifacts.transforms)
    assert geometry[0].to_ocr != [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert artifacts.preparation["pages_detail"][0]["perspective_corrigee"] is True
    assert artifacts.ocr_pdf is not None


# ---------------------------------------------------------------- chaîne complète


def image_runtime(settings, photo: bytes, name="lot1/photo.jpg"):
    """Runtime de test avec la photo déposée dans incoming/."""
    blobs = FakeBlobService()
    blobs.put("incoming", name, photo)
    svc = services(detector=FakeDetector({"DUPONT": "NOM", "Jean": "PRENOM"}))
    return Runtime(settings=settings, services=svc, mapping_store=MemoryStore(), blob_service=blobs)


def test_photo_is_pseudonymised_in_its_original_format_and_framing(settings):
    photo, p = make_photo()
    runtime = image_runtime(settings, photo)
    ocr_holder = {}

    def prepare(job_json, ctx):
        """Prépare l'image puis branche l'OCR simulé sur la géométrie obtenue."""
        job = handle_image_job(job_json, ctx)
        geometry = load_geometry(ctx.blob_service.data("work", job.prepared.transforms))
        ocr_holder["ocr"] = runtime.services.ocr = PhotoOcr(p, geometry)
        return job

    status, messages = run_chain(runtime, "lot1/photo.jpg", prepare_image=prepare)
    assert status == "ok" and len(messages) == 2
    blobs = runtime.blob_service
    assert blobs.names("pseudonymised") == ["lot1/photo.jpg", "lot1/photo.pseudonymise.txt", "lot1/photo.rapport.json"]
    out = Image.open(io.BytesIO(blobs.data("pseudonymised", "lot1/photo.jpg")))
    assert out.format == "JPEG" and out.size == PHOTO_SIZE and len(out.getexif()) == 0
    text = blobs.data("pseudonymised", "lot1/photo.pseudonymise.txt").decode()
    assert "DUPONT" not in text and "[NOM_" in text
    assert ocr_holder["ocr"].calls == 1
    assert blobs.names("work") == []  # fichiers intermédiaires supprimés

    # Le masque retombe sur le mot « DUPONT » de la photo d'origine (quadrilatère vu en biais).
    word = next(w for _, ws in truth_words_px() for w in ws if w.text == "DUPONT")
    x0, y0, x1, y1 = word.rect
    pixels = np.asarray(out.convert("L"))
    samples = [
        pixels[int(round(py)), int(round(px))]
        for fx in np.linspace(0.15, 0.85, 6)
        for fy in (0.2, 0.8)
        for px, py in [apply(p.tolist(), x0 + fx * (x1 - x0), y0 + fy * (y1 - y0))]
    ]
    assert np.mean([abs(int(v) - FILL) < 30 for v in samples]) > 0.7

    trace = runtime.mapping_store.traces[-1]
    assert (trace["origin"], trace["sourceFormat"], trace["stage"]) == ("image", "jpeg", "pseudonymisation")
    assert trace["preparation"]["pages_detail"][0]["perspective_corrigee"] is True
    report = json.loads(blobs.data("pseudonymised", "lot1/photo.rapport.json"))
    assert report["source"]["origine"] == "image"


def test_blurry_photo_goes_to_quarantine_unchanged(settings):
    photo, _ = make_photo(blur=6)
    runtime = image_runtime(settings, photo)
    status, _ = run_chain(runtime, "lot1/photo.jpg", prepare_image=handle_image_job)
    assert status == "quarantine"
    blobs = runtime.blob_service
    assert blobs.names("quarantine") == ["non-classe/lot1/photo.jpg", "non-classe/lot1/photo.jpg.motif.json"]
    assert blobs.data("quarantine", "non-classe/lot1/photo.jpg") == photo
    motif = json.loads(blobs.data("quarantine", "non-classe/lot1/photo.jpg.motif.json"))
    assert motif["motifs"] == ["image_floue"] and motif["etape"] == "preparation_image"
    assert blobs.names("work") == []
