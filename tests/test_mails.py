"""Mails .eml : un dossier pseudonymisé par mail (mail.json, body.html|txt, pièces jointes, rapport)."""

import json
import re
from email.message import EmailMessage
from pathlib import Path

import pytest

from fakes import DEPOSIT as D, FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.exceptions import Exceptions, parse_exceptions
from pseudo.images.prepare import handle_image_job
from pseudo.mails.html_body import HtmlBody
from pseudo.mails.parse import parse_eml
from pseudo.prompts import LocalPromptSource, PromptRepository
from pseudo.router import route_event
from pseudo.runtime import Runtime
from test_classification import FakeClassifier, scores
from test_images import make_photo
from test_pipeline import FakeDetector, make_text_pdf, services

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
DICTIONARY = {"DUPONT": "NOM", "Jean": "PRENOM", "Durand": "NOM", "Marie": "PRENOM"}
SECRETS = ("DUPONT", "Dupont", "dupont", "Durand", "durand", "06 12 34 56 78")


@pytest.fixture
def settings(monkeypatch):
    """Mails activés, valeurs fictives, images activées."""
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    monkeypatch.setenv("MAIL_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    monkeypatch.delenv("OCR_NATIVE_PAGES", raising=False)
    return Settings.from_env()


def make_mail(attachments=(("facture_DUPONT_Jean.pdf", make_text_pdf()),), html=True) -> bytes:
    """Mail fictif : patient, organisation de la liste (adresse générique et nominative), corps HTML."""
    msg = EmailMessage()
    msg["From"] = "Jean DUPONT <jean.dupont@gmail.com>"
    msg["To"] = "SG Santé <contact@sgsante.fr>"
    msg["Cc"] = "Marie Durand <marie.durand@sgsante.fr>"
    msg["Subject"] = "Facture de Jean DUPONT"
    msg["Date"] = "Wed, 14 Oct 2026 09:30:00 +0200"
    msg["Message-ID"] = "<abc123@example.net>"
    msg.set_content("Bonjour,\nCi-joint la facture de Jean DUPONT.\nTél. 06 12 34 56 78\n")
    if html:
        msg.add_alternative(
            '<html><head><meta charset="iso-8859-1"><script>alert("Jean DUPONT")</script></head><body>'
            "<p>Bonjour,</p><p>Ci-joint la facture de <b>Jean DUPONT</b>.</p><p>Tél. 06 12 34 56 78</p>"
            '<a href="mailto:jean.dupont@gmail.com" title="Écrire à Jean DUPONT">écrire</a>'
            '<img src="https://suivi.example.net/p.gif?u=jean.dupont@gmail.com">'
            "<!-- relu par Jean DUPONT --></body></html>",
            subtype="html",
        )
    for name, data in attachments:
        maintype, subtype = ("application", "pdf") if name.endswith(".pdf") else ("application", "octet-stream")
        if name.endswith(".jpg"):
            maintype, subtype = "image", "jpeg"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def runtime(settings, mail: bytes, name: str = "lot1/mail.eml") -> Runtime:
    """Runtime de test : le mail est déposé dans incoming/, référentiel réel (exceptions comprises)."""
    blobs = FakeBlobService()
    blobs.put("incoming", name, mail)
    svc = services(
        detector=FakeDetector(DICTIONARY, only_first=False),
        classifier=FakeClassifier(scores(("DENTAIRE", 0.9))),
        fields=PromptRepository(LocalPromptSource(PROMPTS)).catalog,
    )
    return Runtime(settings=settings, services=svc, mapping_store=MemoryStore(), blob_service=blobs)


def published(rt: Runtime) -> dict[str, bytes]:
    """Fichiers publiés : chemin -> contenu."""
    return {n: rt.blob_service.data("pseudonymised", n) for n in rt.blob_service.names("pseudonymised")}


# ---------------------------------------------------------------- de bout en bout


def test_mail_is_published_as_one_folder(settings):
    rt = runtime(settings, make_mail())
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "ok"
    files = published(rt)
    folder = {n.rsplit("/", 1)[0] for n in files}
    assert len(folder) == 1
    folder = folder.pop()
    assert folder == f"{D}/domain=dentaire/lot1/mail"  # nom du fichier .eml d'origine, sans l'extension
    names = {n.rsplit("/", 1)[1] for n in files}
    assert {"mail.json", "body.html", "body.pseudonymise.txt", "rapport.json"} <= names and len(names) == 6
    attachment = next(n for n in names if n.endswith(".pdf"))
    assert "DUPONT" not in attachment and "Jean" not in attachment  # nom du fichier pseudonymisé
    # Comme pour un document seul : texte pseudonymisé de la pièce, à côté d'elle.
    assert attachment.removesuffix(".pdf") + ".pseudonymise.txt" in names

    # Aucune donnée d'origine nulle part (corps, en-têtes, pièce jointe, rapport).
    import pymupdf

    pdf_text = pymupdf.open(stream=files[f"{folder}/{attachment}"], filetype="pdf")[0].get_text()
    for path, data in files.items():
        text = pdf_text if path.endswith(".pdf") else data.decode("utf-8")
        for secret in SECRETS:
            assert secret not in text, (path, secret)
    assert rt.blob_service.names("work") == []  # dossier de travail purgé


def test_mail_json_keeps_partner_organisations(settings):
    rt = runtime(settings, make_mail())
    run_chain(rt, "lot1/mail.eml")
    files = published(rt)
    mail = json.loads(next(v for k, v in files.items() if k.endswith("mail.json")))
    assert mail["a"] == [{"nom": "SG Santé", "email": "contact@sgsante.fr"}]  # organisation, adresse générique
    cc = mail["cc"][0]
    assert cc["email"].endswith("@sgsante.fr") and cc["email"] != "marie.durand@sgsante.fr"  # nominative
    assert cc["nom"] and "Durand" not in cc["nom"]
    sender = mail["de"][0]
    assert sender["email"].endswith("@example.com") and "DUPONT" not in sender["nom"]
    assert "DUPONT" not in mail["objet"] and mail["objet"].startswith("Facture de ")
    assert mail["date"].startswith("2026-10-14") and mail["corps"] == "body.html"


def test_html_body_is_cleaned(settings):
    rt = runtime(settings, make_mail())
    run_chain(rt, "lot1/mail.eml")
    body = next(v for k, v in published(rt).items() if k.endswith("body.html")).decode("utf-8")
    assert "<script" not in body and "alert" not in body  # script retiré
    assert "suivi.example.net" not in body  # image distante retirée
    assert "<!--" not in body  # commentaire retiré
    assert 'charset="utf-8"' in body
    assert re.search(r'href="mailto:[^"]+@example\.com"', body)  # lien mailto pseudonymisé
    assert "<b>" in body and "Bonjour," in body  # mise en forme et texte neutre conservés


def test_mail_without_attachment_has_only_body(settings):
    rt = runtime(settings, make_mail(attachments=(), html=False))
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "ok"
    names = {n.rsplit("/", 1)[1] for n in published(rt)}
    assert names == {"mail.json", "body.txt", "body.pseudonymise.txt", "rapport.json"}


def test_unsupported_attachment_sends_whole_mail_to_quarantine_without_ai(settings):
    rt = runtime(settings, make_mail(attachments=(("compte-rendu_DUPONT.docx", b"PK..."),)))
    calls = []
    rt.services.detector.detect = lambda lines: calls.append(lines) or []
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "quarantine" and calls == []  # aucun appel aux agents IA
    names = rt.blob_service.names("quarantine")
    assert any(n.endswith("/mail.eml") for n in names) and any(n.endswith("/motif.json") for n in names)
    motif = json.loads(rt.blob_service.data("quarantine", next(n for n in names if n.endswith("motif.json"))))
    assert motif["motifs"] == ["piece_jointe_non_prise_en_charge"]
    assert motif["rapport"]["pieces_refusees"][0]["extension"] == ".docx"
    assert "DUPONT" not in json.dumps(motif)  # le motif ne recopie pas le nom du fichier
    assert published(rt) == {}


def test_refused_image_attachment_sends_whole_mail_to_quarantine(settings):
    blurry, _ = make_photo(blur=6)
    mail = make_mail(attachments=(("facture.pdf", make_text_pdf()), ("photo.jpg", blurry)))
    rt = runtime(settings, mail)
    status, messages = run_chain(rt, "lot1/mail.eml", prepare_image=handle_image_job)
    assert status == "quarantine"
    motif = json.loads(next(rt.blob_service.data("quarantine", n) for n in rt.blob_service.names("quarantine") if n.endswith("motif.json")))
    assert "image_floue" in motif["motifs"]
    assert published(rt) == {} and rt.blob_service.names("work") == []


def test_reprocessing_the_same_mail_reuses_its_folder(settings):
    rt = runtime(settings, make_mail())
    run_chain(rt, "lot1/mail.eml")
    first = set(published(rt))
    run_chain(rt, "lot1/mail.eml")
    assert set(published(rt)) == first


# ---------------------------------------------------------------- briques


def test_parse_eml_reads_headers_body_and_attachments():
    mail = parse_eml(make_mail())
    assert mail.subject == "Facture de Jean DUPONT" and mail.body_format == "html"
    assert mail.senders[0].email == "jean.dupont@gmail.com"
    assert [r.email for r in mail.recipients["cc"]] == ["marie.durand@sgsante.fr"]
    assert [(a.filename, a.fmt) for a in mail.attachments] == [("facture_DUPONT_Jean.pdf", "pdf")]


def test_html_fragments_include_hidden_places():
    html = HtmlBody('<p title="Jean DUPONT">Bonjour <b>Jean</b></p><a href="mailto:j.d@x.fr">x</a><script>DUPONT</script>')
    texts = [t for _, t in html.fragments()]
    assert texts == ["Jean DUPONT", "Bonjour ", "Jean", "j.d@x.fr", "x"]  # attribut, textes, mailto ; pas le script


def test_exceptions_parse_and_decide():
    exc = parse_exceptions(
        "## Organisations à ne jamais pseudonymiser\n- **Noms** : SG Santé\n- **Domaines** : sgsante.fr, @ageo.fr\n"
        "- **Adresses génériques** : contact,\n  no, reply\n"
    )
    assert exc.domains == ("sgsante.fr", "ageo.fr")
    assert exc.is_organisation("SG SANTE") and not exc.is_organisation("Jean")
    assert exc.keeps_email("contact@sgsante.fr") and exc.keeps_email("no-reply@ageo.fr")
    assert not exc.keeps_email("marie.durand@sgsante.fr") and not exc.keeps_email("contact@gmail.com")
    assert exc.with_listed_domain("marie.durand@sgsante.fr", "luc.petit@example.com") == "luc.petit@sgsante.fr"
    assert Exceptions().keeps_email("contact@sgsante.fr") is False


def test_exceptions_also_apply_to_pdf_documents(settings):
    """Un PDF qui cite une organisation de la liste : son adresse générique n'est pas masquée."""
    import pymupdf

    from pseudo.pipeline import run_pipeline

    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(["Patient : M. DUPONT Jean", "Contact : contact@sgsante.fr", "Gestionnaire : marie.durand@sgsante.fr"]):
        page.insert_text((72, 72 + 24 * i), text, fontsize=12)
    svc = services(fields=PromptRepository(LocalPromptSource(PROMPTS)).catalog)
    result = run_pipeline(doc.tobytes(), settings, svc)
    out = pymupdf.open(stream=result.output, filetype="pdf")[0].get_text()
    assert "contact@sgsante.fr" in out
    assert "marie.durand@sgsante.fr" not in out and "@sgsante.fr" in out.split("Gestionnaire")[1]


def test_small_inline_logo_is_removed_not_quarantined(settings):
    """Un logo intégré au corps, trop petit pour être lu, est retiré : le mail est quand même publié."""
    import pymupdf
    from email import message_from_bytes, policy

    page = pymupdf.open().new_page(width=120, height=30)
    logo = page.get_pixmap(dpi=72).tobytes("png")
    msg = message_from_bytes(make_mail(), policy=policy.default)
    msg.get_body(("html",)).add_related(logo, maintype="image", subtype="png", cid="<logo@example.com>")
    html = msg.get_body(("html",))
    html.set_content(html.get_content().replace("<p>Bonjour,</p>", '<p><img src="cid:logo@example.com"></p><p>Bonjour,</p>'), subtype="html")
    rt = runtime(settings, msg.as_bytes())
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "ok"
    files = published(rt)
    assert not any(n.endswith(".png") for n in files)  # logo non publié
    report = json.loads(next(v for k, v in files.items() if k.endswith("rapport.json")))
    assert report["corps"]["images_retirees"] == {"gif": 0, "trop_petites": 1, "integrees_illisibles": 0}
    body = next(v for k, v in files.items() if k.endswith("body.html")).decode("utf-8")
    assert "cid:" not in body


def _png(width: int, height: int) -> bytes:
    """Image PNG unie de la taille donnée."""
    import pymupdf

    return pymupdf.open().new_page(width=width, height=height).get_pixmap(dpi=72).tobytes("png")


def _gif(width: int = 1200, height: int = 1200) -> bytes:
    """Image GIF de la taille donnée (assez grande pour ne pas être retirée pour sa taille)."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="GIF")
    return buf.getvalue()


def test_gif_and_small_images_are_removed_whatever_their_type(settings):
    """GIF (même grand) et images de moins de 300 px sur un côté (icône en octet-stream sans nom,
    petite photo jointe avec son nom) : retirés, le mail est publié sans quarantaine."""
    from email import message_from_bytes, policy

    msg = message_from_bytes(make_mail(), policy=policy.default)
    related = msg.get_body(("html",))
    related.add_related(_png(64, 64), maintype="application", subtype="octet-stream", cid="<icone@example.com>")
    related.add_related(_gif(), maintype="image", subtype="gif", cid="<anim@example.com>")
    msg.add_attachment(_png(250, 800), maintype="image", subtype="png", filename="capture.png")
    msg.add_attachment(_gif(), maintype="application", subtype="octet-stream", filename="banniere")
    rt = runtime(settings, msg.as_bytes())
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "ok"
    files = published(rt)
    assert not any(n.endswith((".png", ".gif", ".bin")) or n.endswith("banniere") for n in files)
    report = json.loads(next(v for k, v in files.items() if k.endswith("rapport.json")))
    assert report["corps"]["images_retirees"] == {"gif": 2, "trop_petites": 2, "integrees_illisibles": 0}


def test_mail_image_min_side_is_configurable(settings, monkeypatch):
    """MAIL_IMAGE_MIN_SIDE=0 : aucune image retirée pour sa taille ; l'icône sans nom de type inconnu
    redevient une pièce non prise en charge."""
    from email import message_from_bytes, policy

    monkeypatch.setenv("MAIL_IMAGE_MIN_SIDE", "0")
    msg = message_from_bytes(make_mail(), policy=policy.default)
    msg.get_body(("html",)).add_related(_png(64, 64), maintype="application", subtype="octet-stream", cid="<icone@example.com>")
    rt = runtime(Settings.from_env(), msg.as_bytes())
    status, _ = run_chain(rt, "lot1/mail.eml")
    assert status == "quarantine"


def test_queue_sender_sends_all_messages_in_batches(monkeypatch):
    """Les tâches des pièces jointes partent toutes, en plusieurs lots si un lot est plein."""
    import azure.servicebus

    from pseudo.queues import QueueSender

    sent = []

    class Batch(list):
        def add_message(self, message):
            if len(self) == 2:
                raise ValueError("lot plein")
            self.append(str(message))

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        create_message_batch = staticmethod(Batch)

        def send_messages(self, batch):
            sent.append(list(batch))

    class Client:
        def __init__(self, fully_qualified_namespace, credential):
            self.namespace = fully_qualified_namespace

        def get_queue_sender(self, queue):
            assert queue == "pdf-jobs"
            return Sender()

    monkeypatch.setattr(azure.servicebus, "ServiceBusClient", Client)
    QueueSender("sb.servicebus.windows.net", credential=None).send("pdf-jobs", ["a", "b", "c", "d", "e"])
    assert sent == [["a", "b"], ["c", "d"], ["e"]]
    QueueSender("sb.servicebus.windows.net", credential=None).send("pdf-jobs", [])
    assert len(sent) == 3  # rien à envoyer : aucun lot


def test_text_versions_of_body_and_attachments_are_published(settings):
    """body.pseudonymise.txt (corps en texte brut, même pour un mail HTML) et <pièce>.pseudonymise.txt."""
    rt = runtime(settings, make_mail())
    assert run_chain(rt, "lot1/mail.eml")[0] == "ok"
    files = published(rt)
    body = next(v for k, v in files.items() if k.endswith("/body.pseudonymise.txt")).decode("utf-8")
    assert "<" not in body and "Bonjour," in body and "Ci-joint la facture de" in body  # texte brut, sans balises
    assert "alert" not in body  # le script retiré du HTML n'y revient pas
    part = next(v for k, v in files.items() if k.endswith(".pseudonymise.txt") and "/body." not in k).decode("utf-8")
    assert "Attestation" in part and "===== Page 1 =====" in part  # même format que pour un document seul
    for text in (body, part):
        for secret in SECRETS:
            assert secret not in text


def test_text_names_do_not_collide():
    from pseudo.mails.finalize import _text_name

    used = {"mail.json", "rapport.json"}
    assert _text_name("facture.pdf", used) == "facture.pseudonymise.txt"
    assert _text_name("facture.jpg", used) == "facture.jpg.pseudonymise.txt"


# ---------------------------------------------------------------- texte du mail dans text-jobs


def test_split_calls_no_ai_and_sends_the_mail_text_first(settings):
    from pseudo.mails.split import handle_mail_job
    from pseudo.router import QUEUE_PDF, QUEUE_TEXT

    rt = runtime(settings, make_mail())
    calls = []
    rt.services.detector.detect = lambda lines: calls.append(lines) or []
    route = route_event(event("lot1/mail.eml"), rt)
    children = handle_mail_job(route.job.to_json(), rt)
    assert calls == []  # découpage sans agent IA : une erreur 429 ne peut plus le bloquer
    assert [q for q, _ in children] == [QUEUE_TEXT, QUEUE_PDF]
    assert json.loads(children[0][1])["origin"] == "mail-text"


def test_parts_can_finish_before_the_mail_text(settings):
    from pseudo.handler import handle_pdf_job
    from pseudo.mails.split import handle_mail_job
    from pseudo.router import QUEUE_TEXT
    from pseudo.text_jobs import handle_text_job

    rt = runtime(settings, make_mail())
    route = route_event(event("lot1/mail.eml"), rt)
    children = handle_mail_job(route.job.to_json(), rt)
    text = [body for q, body in children if q == QUEUE_TEXT]
    for queue, body in children:
        if queue != QUEUE_TEXT:
            assert handle_pdf_job(body, rt) == "ok"  # pièce prête, mail pas encore publié
    assert published(rt) == {}
    assert handle_text_job(text[0], rt) == "ok"  # la dernière partie publie le mail
    names = {n.rsplit("/", 1)[1] for n in published(rt)}
    pdf = next(n for n in names if n.endswith(".pdf"))
    assert "DUPONT" not in pdf and pdf.removesuffix(".pdf") + ".pseudonymise.txt" in names


def test_json_arrived_before_the_mail_is_attached_when_the_mail_is_published(settings, monkeypatch):
    from test_json_metadata import GED
    from pseudo.text_jobs import handle_text_job

    monkeypatch.setenv("JSON_METADATA_ENABLED", "true")
    rt = runtime(Settings.from_env(), make_mail())
    rt.blob_service.put("incoming", "lot1/mail.eml.json", json.dumps(GED).encode())
    route = route_event(event("lot1/mail.eml.json"), rt)
    assert handle_text_job(route.job.to_json(), rt) == "en_attente"  # le mail n'est pas encore traité
    assert run_chain(rt, "lot1/mail.eml")[0] == "ok"
    assert f"{D}/domain=dentaire/lot1/mail/mail.eml.json" in published(rt)  # rattaché par la finalisation du mail
