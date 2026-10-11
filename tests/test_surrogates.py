"""Mode substitution : valeurs fictives réalistes à la place des jetons dans le document publié."""

import io
import json
import re

import numpy as np
import pymupdf
import pytest
from PIL import Image

from fakes import FakeBlobService, MemoryStore, run_chain
from pseudo.config import Settings
from pseudo.fields import FieldCatalogError, parse_fields
from pseudo.geometry import apply, load_geometry
from pseudo.images.prepare import handle_image_job
from pseudo.pipeline import run_pipeline
from pseudo.pseudonymizer import ValueCipher, normalize
from pseudo.runtime import Runtime
from pseudo.surrogates import SurrogateFactory
from pseudo.surrogates.generators import _communes, nir_key
from test_images import PhotoOcr, make_photo, truth_words_px
from test_pipeline import FakeDetector, make_text_pdf, output_text, services

FACTORY = SurrogateFactory(b"k" * 32, current_year=2026)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    return Settings.from_env()


def gen(generator, value):
    return FACTORY.generate(generator, value, key=value)


# ---------------------------------------------------------------- générateurs


@pytest.mark.parametrize(
    "value, pattern",
    [
        ("12/03/1985", r"\d{2}/\d{2}/\d{4}"),
        ("12.03.85", r"\d{2}\.\d{2}\.\d{2}"),
        ("1985-03-12", r"\d{4}-\d{2}-\d{2}"),
        ("8 septembre 1975", r"\d{1,2} [a-zéû]+ \d{4}"),
    ],
)
def test_birth_date_keeps_its_format(value, pattern):
    out = gen("date_naissance", value)
    assert re.fullmatch(pattern, out) and out != value


def test_birth_date_and_nir_are_consistent_even_generated_separately():
    date = gen("date_naissance", "12/03/1985")
    nir = gen("nir", "1 85 03 69 123 456 10")
    day, month, year = date.split("/")
    compact = nir.replace(" ", "")
    assert compact[1:3] == year[2:] and compact[3:5] == month
    assert compact[13:] == nir_key(compact[:13])  # clé valide
    assert compact[0] == "1"  # sexe conservé
    assert re.fullmatch(r"\d \d{2} \d{2} \d{2} \d{3} \d{3} \d{2}", nir)  # même mise en forme


@pytest.mark.parametrize(
    "value, pattern",
    [
        ("06 12 34 56 78", r"06 39 98 \d{2} \d{2}"),
        ("04.65.71.12.34", r"04\.65\.71\.\d{2}\.\d{2}"),
        ("+33 6 12 34 56 78", r"\+33 6 39 98 \d{2} \d{2}"),
        ("0033612345678", r"0033639\d{6}"),
    ],
)
def test_phone_numbers_use_fiction_ranges_and_keep_format(value, pattern):
    assert re.fullmatch(pattern, gen("telephone", value))


def test_email_name_and_address():
    assert gen("email", "julien.lefevre@example.com").endswith("@example.com")
    assert gen("nom", "DUPONT").isupper() and gen("nom", "DUPONT") != "DUPONT"
    assert gen("prenom", "J.").endswith(".") and len(gen("prenom", "J.")) == 2
    address = gen("adresse", "27 rue Paul Bert, Bât. B, Apt 12, 69003 Lyon")
    cp, city = re.search(r"(\d{5}) ([^,]+)$", address).groups()
    assert (cp, city) in _communes()  # vrai couple code postal / ville
    assert "rue " in address and "Bât. B" in address and "Paul Bert" not in address


def test_values_are_deterministic_and_key_dependent():
    other = SurrogateFactory(b"z" * 32, current_year=2026)
    assert gen("nom", "DUPONT") == gen("nom", "DUPONT")
    assert other.generate("telephone", "06 12 34 56 78", key="06 12 34 56 78") != gen("telephone", "06 12 34 56 78")


def test_surrogate_never_equals_a_real_value_of_the_document():
    first = FACTORY.generate("nom", "DUPONT", key="k")
    forbidden = {normalize("texte", "DUPONT"), normalize("texte", first)}
    out = FACTORY.unique("nom", "DUPONT", key="k", forbidden=forbidden, normalize=lambda v: normalize("texte", v))
    assert normalize("texte", out) not in forbidden


def test_unknown_generator_is_rejected():
    with pytest.raises(FieldCatalogError, match="substitution"):
        parse_fields("## IBAN — Compte\n- **Substitution** : iban\n")
    assert parse_fields("## IBAN — Compte\n").substitution("IBAN") == "generique"


# ---------------------------------------------------------------- PDF


def test_pdf_is_published_with_realistic_values(settings):
    svc = services()
    result = run_pipeline(make_text_pdf(), settings, svc)
    assert result.status == "ok" and result.report["mode_sortie"] == "substitution"
    published = output_text(result.output)
    for secret in ("DUPONT", "Jean", "12/03/1985", "06 12 34 56 78"):
        assert secret not in published and secret not in result.text
    assert "[NOM_" not in published and "[NOM_" not in result.text  # aucun jeton publié
    surrogate = next(o["valeur_fictive"] for o in result.occurrences if o["type"] == "NOM")
    assert surrogate in published
    # Même valeur d'origine -> même valeur fictive (les 2 occurrences de DUPONT).
    assert [o["valeur_fictive"] for o in result.occurrences if o["type"] == "NOM"] == [surrogate, surrogate]
    phone = next(o["valeur_fictive"] for o in result.occurrences if o["type"] == "TELEPHONE")
    assert phone.startswith("06 39 98") and phone in published
    assert all(o["position"] and o["page"] == 1 for o in result.occurrences)


def test_reidentification_from_document_and_fake_value(settings):
    """Document + valeur fictive -> occurrence -> jeton -> valeur d'origine chiffrée -> déchiffrement."""
    svc = services()
    result = run_pipeline(make_text_pdf(), settings, svc)
    occurrence = next(o for o in result.occurrences if o["type"] == "NOM")
    record = next(r for r in result.records if r.token.strip("[]") == occurrence["jeton"])
    assert svc.cipher.decrypt(record.token, record.ciphertext) == "DUPONT"


def test_token_mode_still_publishes_tokens(settings, monkeypatch):
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "jetons")
    result = run_pipeline(make_text_pdf(), Settings.from_env(), services())
    assert "[NOM_" in output_text(result.output)
    assert all(o["valeur_fictive"] is None for o in result.occurrences)


# ---------------------------------------------------------------- images


def test_photo_is_published_with_realistic_values_and_pixel_positions(settings):
    photo, p = make_photo()
    blobs, store = FakeBlobService(), MemoryStore()
    blobs.put("incoming", "lot1/photo.jpg", photo)
    svc = services(detector=FakeDetector({"DUPONT": "NOM", "Jean": "PRENOM"}))
    runtime = Runtime(settings=settings, services=svc, mapping_store=store, blob_service=blobs)

    def prepare(job_json, ctx):
        job = handle_image_job(job_json, ctx)
        svc.ocr = PhotoOcr(p, load_geometry(ctx.blob_service.data("work", job.prepared.transforms)))
        return job

    status, _ = run_chain(runtime, "lot1/photo.jpg", prepare_image=prepare)
    assert status == "ok"
    text = blobs.data("pseudonymised", "lot1/photo.pseudonymise.txt").decode()
    assert "DUPONT" not in text and "[NOM_" not in text

    occurrence = next(o for o in store.occurrences if o["type"] == "NOM")
    assert occurrence["valeur_fictive"] in text
    assert occurrence["document"] == "pseudonymised/lot1/photo.jpg" and occurrence["position_pixels"]

    # La zone de « DUPONT » n'est plus grise : fond clair et texte sombre (valeur fictive lisible).
    out = np.asarray(Image.open(io.BytesIO(blobs.data("pseudonymised", "lot1/photo.jpg"))).convert("L"))
    word = next(w for _, ws in truth_words_px() for w in ws if w.text == "DUPONT")
    x0, y0, x1, y1 = word.rect
    samples = [
        int(out[int(round(py)), int(round(px))])
        for fx in np.linspace(0.05, 0.95, 12)
        for fy in np.linspace(0.1, 0.9, 6)
        for px, py in [apply(p.tolist(), x0 + fx * (x1 - x0), y0 + fy * (y1 - y0))]
    ]
    assert np.mean([v > 230 for v in samples]) > 0.4  # fond clair (feuille blanche)
    assert min(samples) < 120  # du texte sombre a été écrit
    assert not any(abs(v - 217) < 6 for v in samples[:3]) or np.mean([abs(v - 217) < 6 for v in samples]) < 0.3
