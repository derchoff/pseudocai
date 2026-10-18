"""scripts/statistiques.py : calculs sur des fiches réelles (produites par le traitement), écran et Excel."""

import importlib.util
import json
from pathlib import Path

from openpyxl import load_workbook

from fakes import FakeBlobService, MemoryStore, event, run_chain
from pseudo.router import route_event
from test_classification import make_runtime
from test_mails import make_mail, runtime as mail_runtime
from test_pipeline import make_text_pdf, services
from test_statistics import settings  # noqa: F401 - fixture

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "statistiques.py"
spec = importlib.util.spec_from_file_location("statistiques", SCRIPT)
statistiques = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statistiques)


def fiches(settings) -> list[dict]:
    """Fiches d'une petite journée : un PDF publié (traité deux fois), un mail avec une pièce, un ZIP refusé."""
    rt = make_runtime(settings, services())
    run_chain(rt, "lot1/facture.pdf")
    run_chain(rt, "lot1/facture.pdf")  # retraitement
    rt.blob_service.put("incoming", "lot1/archive.zip", b"PK")
    route_event(event("lot1/archive.zip"), rt)
    mail = mail_runtime(settings, make_mail())
    run_chain(mail, "lot1/mail.eml")
    return list(rt.mapping_store.statistics.values()) + list(mail.mapping_store.statistics.values())


def table(tables, name) -> dict:
    """Tableau sous forme {libellé ou 1re colonne: ligne}."""
    headers, rows = tables[name]
    return {r[0]: dict(zip(headers, r)) for r in rows}


def test_summary_counts_documents_pages_and_quarantine(settings):
    rows = fiches(settings)
    summary = {k: v["Valeur"] for k, v in table(statistiques.compute(rows, "jour", {"ocr": 0, "entree": 0, "sortie": 0, "language": 0}), "Synthèse").items()}
    assert summary["Documents déposés (un mail = 1)"] == 4  # 2 traitements du PDF + ZIP + mail
    assert summary["dont mails"] == 1 and summary["Documents en quarantaine"] == 1
    assert summary["% de mise en quarantaine"] == 25.0
    assert summary["Documents traités plus d'une fois"] == 1
    assert summary["Pages traitées (documents et pièces)"] == 3  # 2 x 1 page + 1 page de pièce
    assert summary["Pages passées par l'OCR (Document Intelligence)"] == 3
    assert summary["Pages passées par les agents IA"] == 3  # le ZIP refusé n'y passe pas
    assert summary["Textes passés par les agents IA (corps de mail, JSON)"] == 0  # agents simulés : pas d'appel compté
    assert summary["Coût estimé"] is None  # pas de prix


def test_by_type_and_rounds_and_cost(settings):
    rows = fiches(settings)
    prices = {"ocr": 1.5, "entree": 0.25, "sortie": 2.0, "language": 1.0}
    tables = statistiques.compute(rows, "mois", prices)
    by_type = {(r[0], r[1]): r for r in tables["Par type"][1]}
    assert by_type[("PDF hors mail", "application/pdf")][2] == 2
    assert by_type[("PDF dans un mail", "application/pdf")][2] == 1
    assert by_type[("Fichier refusé au routage", "application/zip")][4] == 1  # en quarantaine
    [rounds] = tables["Tours de vérification"][1]
    assert rounds[0] == "2026-03" and rounds[1] == 4  # 2 PDF, texte du mail, pièce
    summary = {k: v["Valeur"] for k, v in table(tables, "Synthèse").items()}
    assert summary["Coût estimé"] is not None and summary["Coût estimé"] >= 0


def test_main_prints_and_writes_the_workbook(settings, tmp_path, capsys):
    export = tmp_path / "fiches.json"
    export.write_text(json.dumps(fiches(settings)), encoding="utf-8")
    excel = tmp_path / "stats.xlsx"
    code = statistiques.main(["--fiches", str(export), "--debut", "2026-03-01", "--fin", "2026-03-31", "--excel", str(excel)])
    assert code == 0
    out = capsys.readouterr().out
    assert "% de mise en quarantaine" in out and "Tours de vérification" in out
    wb = load_workbook(excel)
    assert wb.sheetnames == ["Synthèse", "Par période", "Par type", "Tours de vérification", "Agents IA", "Documents"]
    assert wb["Documents"].max_row == 5  # en-tête + 4 documents


def test_cosmos_account_name_or_url():
    assert statistiques.cosmos_endpoint("cosmos-pseudo-dev-ab12") == "https://cosmos-pseudo-dev-ab12.documents.azure.com:443/"
    assert statistiques.cosmos_endpoint("https://x.documents.azure.com:443/") == "https://x.documents.azure.com:443/"
