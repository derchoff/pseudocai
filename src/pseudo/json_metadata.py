"""JSON de métadonnées de la GED (<nom du document>.json) : pseudonymisation champ par champ.

    {"GED_metadata": {"description": "Décès salarié - DUPONT Jean", "size": 754854, …}}
        │  valeurs texte à analyser -> une ligne « chemin : valeur » (le chemin donne le contexte)
        ▼
    GED_metadata.description : Décès salarié - DUPONT Jean        ──► pipeline (agents IA, règles…)
        │  valeur pseudonymisée relue derrière le chemin
        ▼
    {"GED_metadata": {"description": "Décès salarié - MEYER Bernard", "size": 754854, …}}

- La structure ne change pas : mêmes clés, même ordre ; nombres, booléens et null intacts.
- Les champs de la section « Champs JSON à ne jamais modifier » sont recopiés sans passer par
  les agents IA ; les libellés de service (« Gestion Prestations Individuelles » dans un champ
  « Gestionnaire dédié … ») aussi. Voir json_rules.py.
- Une valeur sur plusieurs lignes donne une ligne analysée par ligne de texte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from .json_rules import ARRAY, JsonRules
from .mails.document import text_line
from .models import DocumentContent, PageContent, Span
from .pipeline import TEXT, PipelineInput, PipelineResult, run_pipeline
from .spans import pseudonymise_document


class JsonMetadataError(ValueError):
    """Le fichier n'est pas un JSON lisible."""


@dataclass
class JsonText:
    """Valeurs à analyser d'un JSON, sous forme de lignes « chemin : valeur »."""

    content: DocumentContent
    # Pour chaque valeur analysée : chemin réel (clés et indices), puis ses lignes (id, ou None pour
    # une ligne vide, et texte d'origine).
    values: list[tuple[tuple, list[tuple[str | None, str]]]] = field(default_factory=list)
    prefixes: dict[str, str] = field(default_factory=dict)  # id de ligne -> « chemin : »
    kept: int = 0  # valeurs texte recopiées sans analyse


@dataclass
class JsonResult:
    """JSON pseudonymisé (None si le pipeline l'a mis en doute) et résultat du pipeline."""

    output: bytes | None
    status: str
    reasons: list[str]
    report: dict
    pipeline: PipelineResult | None = None
    # Valeurs remplacées : jeton, type, valeur fictive et chemin du champ (ré-identification).
    occurrences: list[dict] = field(default_factory=list)


def _leaves(node, path: tuple = ()):
    """Valeurs du JSON : (chemin réel, valeur) ; un tableau donne un indice dans le chemin réel."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _leaves(value, path + (i,))
    else:
        yield path, node


def _pattern_path(path: tuple) -> tuple[str, ...]:
    """Chemin réel -> chemin du référentiel (indices de tableau remplacés par [])."""
    return tuple(ARRAY if isinstance(k, int) else k for k in path)


def _display(path: tuple) -> str:
    """Chemin lisible pour l'agent IA : GED_metadata.ancestors[2].label."""
    out = ""
    for key in path:
        out += f"[{key}]" if isinstance(key, int) else (f".{key}" if out else key)
    return out


def build_json_text(data, rules: JsonRules) -> JsonText:
    """Lignes à analyser : une par ligne de texte de chaque valeur non conservée."""
    lines = []
    built = JsonText(content=DocumentContent(pages=[]))
    for path, value in _leaves(data):
        if not isinstance(value, str) or not value.strip():
            continue
        pattern = _pattern_path(path)
        if rules.is_kept(pattern) or rules.is_service_label(pattern, value):
            built.kept += 1
            continue
        prefix = f"{_display(path)} : "
        rows = []
        for segment in value.split("\n"):
            if not segment.strip():
                rows.append((None, segment))
                continue
            line_id = f"p1-l{len(lines) + 1}"
            lines.append(text_line(line_id, prefix + segment))
            built.prefixes[line_id] = prefix
            rows.append((line_id, segment))
        built.values.append((path, rows))
    built.content = DocumentContent(pages=[PageContent(index=0, source="native", lines=lines)])
    return built


def read_back(data, built: JsonText, spans: list[Span]):
    """Copie du JSON avec les valeurs pseudonymisées (valeurs fictives ou jetons selon le mode)."""
    kept_spans = []
    for s in spans:
        start = len(built.prefixes.get(s.line_id, ""))
        if s.end <= start:
            continue  # zone entièrement dans le chemin : le chemin n'est jamais modifié
        kept_spans.append(replace(s, start=max(s.start, start)))
    pseudo = {pl.id: pl.text for pl in pseudonymise_document(built.content, kept_spans)}
    out = json.loads(json.dumps(data))  # copie profonde
    for path, rows in built.values:
        parts = []
        for line_id, original in rows:
            if line_id is None:
                parts.append(original)
                continue
            text, prefix = pseudo.get(line_id, ""), built.prefixes[line_id]
            parts.append(text[len(prefix):] if text.startswith(prefix) else original)
        _set(out, path, "\n".join(parts))
    return out


def _set(node, path: tuple, value) -> None:
    """Remplace la valeur au chemin réel donné."""
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def pseudonymise_json(data: bytes, settings, services, rules: JsonRules) -> JsonResult:
    """Pseudonymise un JSON de métadonnées. Lève JsonMetadataError s'il n'est pas lisible.

    Le JSON n'est pas classé : il suit toujours le classement de son document.
    """
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JsonMetadataError(str(exc)) from exc
    built = build_json_text(parsed, rules)
    report = {"champs_conserves": built.kept, "champs_analyses": len(built.values)}
    if not built.values:
        return JsonResult(_dump(parsed), "ok", [], report)
    result = run_pipeline(
        PipelineInput(pdf=b"", content=built.content, source={"origine": "json-metadata", "format": "json"}),
        settings,
        services,
        steps=TEXT,
    )
    report = {**result.report, **report}
    output = _dump(read_back(parsed, built, result.spans)) if result.status == "ok" else None
    return JsonResult(output, result.status, result.reasons, report, result, _occurrences(built, result.occurrences))


def _occurrences(built: JsonText, occurrences: list[dict]) -> list[dict]:
    """Occurrences du pipeline, repérées par le chemin du champ (pas de position sur une page)."""
    out = []
    for occ in occurrences:
        prefix = built.prefixes.get(occ.get("ligne"), "")
        start, end = occ.get("caracteres", [0, 0])
        out.append({
            "jeton": occ["jeton"], "type": occ["type"], "valeur_fictive": occ["valeur_fictive"],
            "chemin": prefix.removesuffix(" : "), "caracteres": [max(0, start - len(prefix)), max(0, end - len(prefix))],
        })
    return out


def _dump(data) -> bytes:
    """JSON lisible, accents conservés, indentation de 4 comme la GED."""
    return json.dumps(data, ensure_ascii=False, indent=4).encode("utf-8")
