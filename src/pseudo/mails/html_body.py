"""Corps HTML d'un mail : textes à analyser, puis HTML reconstruit avec les remplacements.

Seuls les textes changent ; la structure et la mise en forme sont conservées. Sont aussi traités
les endroits où une donnée personnelle se cache dans le balisage : attributs title, alt et
aria-label, liens (mailto: et adresses web, dont les paramètres peuvent contenir un e-mail).

Retirés par sécurité : scripts, gestionnaires d'événements (onclick…), commentaires, liens
javascript:, images distantes ou en data: (suivi d'ouverture, contenu non analysé). Une image
intégrée (src="cid:…") pointe vers le fichier de la pièce pseudonymisée, dans le même dossier.
Le fichier produit est en UTF-8 (déclaration de jeu de caractères réécrite).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

TEXT_ATTRIBUTES = ("title", "alt", "aria-label")
DROPPED_ATTRIBUTES = ("srcset", "background", "ping", "formaction", "action")


@dataclass
class _Token:
    """Élément du HTML d'origine, dans l'ordre : balise ouvrante, fermante, texte ou brut."""
    kind: str  # "start" | "startend" | "end" | "data" | "raw"
    tag: str = ""
    attrs: list | None = None  # [[nom, valeur]]
    text: str = ""


class _Tokenizer(HTMLParser):
    """Découpe le HTML en jetons ; scripts et commentaires sont écartés, styles gardés tels quels."""

    def __init__(self):
        """Liste de jetons vide ; compteurs d'imbrication des <script> et <style>."""
        super().__init__(convert_charrefs=True)
        self.tokens: list[_Token] = []
        self._script = 0
        self._style = 0

    def handle_starttag(self, tag, attrs):
        """Balise ouvrante (un <script> ouvre une zone ignorée)."""
        if tag == "script":
            self._script += 1
            return
        if tag == "style":
            self._style += 1
        if not self._script:
            self.tokens.append(_Token("start", tag, [[k, v] for k, v in attrs]))

    def handle_startendtag(self, tag, attrs):
        """Balise auto-fermante (<br/>, <img/>)."""
        if not self._script and tag != "script":
            self.tokens.append(_Token("startend", tag, [[k, v] for k, v in attrs]))

    def handle_endtag(self, tag):
        """Balise fermante."""
        if tag == "script":
            self._script = max(0, self._script - 1)
            return
        if tag == "style":
            self._style = max(0, self._style - 1)
        if not self._script:
            self.tokens.append(_Token("end", tag))

    def handle_data(self, data):
        """Texte : analysé, sauf dans un <style> (recopié tel quel)."""
        if self._script:
            return
        self.tokens.append(_Token("raw" if self._style else "data", text=data))

    def handle_decl(self, decl):
        """<!DOCTYPE …> recopié."""
        self.tokens.append(_Token("raw", text=f"<!{decl}>"))


class HtmlBody:
    """HTML d'un corps de mail, découpé en fragments de texte remplaçables."""

    def __init__(self, html: str):
        """Découpe le HTML d'origine en jetons."""
        parser = _Tokenizer()
        parser.feed(html)
        parser.close()
        self._tokens = parser.tokens

    def fragments(self) -> list[tuple[tuple, str]]:
        """Fragments à analyser, dans l'ordre de lecture : (clé, texte). Une clé repère le fragment
        pour le rendu ; les lignes vides ne sont pas des fragments."""
        out = []
        for t, token in enumerate(self._tokens):
            if token.kind == "data":
                for n, segment in enumerate(token.text.split("\n")):
                    if segment.strip():
                        out.append((("data", t, n), segment))
            elif token.kind in ("start", "startend"):
                for a, (name, value) in enumerate(token.attrs):
                    if not value:
                        continue
                    if name in TEXT_ATTRIBUTES:
                        out.append((("attr", t, a), value))
                    elif name == "href" and value.lower().startswith(("mailto:", "http:", "https:")):
                        out.append((("attr", t, a), value[7:] if value.lower().startswith("mailto:") else value))
        return out

    def render(self, replacements: dict[tuple, str], cid_files: dict[str, str]) -> str:
        """HTML reconstruit : fragments remplacés, images intégrées redirigées vers leur fichier."""
        parts = []
        for t, token in enumerate(self._tokens):
            if token.kind == "raw":
                parts.append(token.text)
            elif token.kind == "end":
                parts.append(f"</{token.tag}>")
            elif token.kind == "data":
                segments = token.text.split("\n")
                parts.append("\n".join(
                    escape(replacements.get(("data", t, n), segment), quote=False) for n, segment in enumerate(segments)
                ))
            else:
                attrs = self._attributes(t, token, replacements, cid_files)
                closing = " /" if token.kind == "startend" else ""
                parts.append(f"<{token.tag}{attrs}{closing}>")
        return "".join(parts)

    @staticmethod
    def _attributes(t: int, token: _Token, replacements: dict[tuple, str], cid_files: dict[str, str]) -> str:
        """Attributs réécrits : textes remplacés, liens et sources nettoyés, jeu de caractères UTF-8."""
        out = []
        names = {name for name, _ in token.attrs}
        for a, (name, value) in enumerate(token.attrs):
            lname = name.lower()
            if lname.startswith("on") or lname in DROPPED_ATTRIBUTES:
                continue
            if value is not None and ("attr", t, a) in replacements:
                new = replacements[("attr", t, a)]
                value = f"mailto:{new}" if lname == "href" and value.lower().startswith("mailto:") else new
            if lname == "href" and value and value.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
                continue
            if lname == "src" and value is not None:
                if value.lower().startswith("cid:") and value[4:].strip("<>") in cid_files:
                    value = cid_files[value[4:].strip("<>")]
                else:
                    continue  # image distante, data: ou cid inconnu : retirée
            if token.tag == "meta" and lname == "charset":
                value = "utf-8"
            if token.tag == "meta" and lname == "content" and "http-equiv" in names and "charset" in (value or "").lower():
                value = "text/html; charset=utf-8"
            out.append(f' {name}="{escape(value, quote=True)}"' if value is not None else f" {name}")
        return "".join(out)
