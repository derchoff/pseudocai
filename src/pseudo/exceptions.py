"""Exceptions du référentiel des champs : organisations et mots à ne jamais pseudonymiser.

    ## Organisations à ne jamais pseudonymiser
    - **Noms** : SG Santé, Howden
    - **Domaines** : sgsante.fr, howden-france.com
    - **Adresses génériques** : contact, info, gestion, sinistres

La même section est envoyée aux agents IA (avec le reste du référentiel) et appliquée par le code
APRÈS toutes les détections (agent IA, règles, Azure AI Language), dans tous les documents :
- un nom d'organisation de la liste n'est pas pseudonymisé ;
- une adresse générique d'un domaine de la liste (contact@…, sinistres@…) est conservée ;
- une adresse nominative d'un domaine de la liste (marie.durand@sgsante.fr) reste pseudonymisée,
  mais garde son domaine : seule la partie avant @ reçoit une valeur fictive.

    ## Mots à ne jamais pseudonymiser
    - **Civilités et titres** : Dr, Docteur, Mme
    - **Mots courants** : devis, facture

Tous les mots de cette section (quelle que soit la puce) sont appliqués de la même façon, après
toutes les détections et la propagation :
- une zone qui ne contient que des mots de la liste est retirée (« DEVIS » n'est jamais un prénom) ;
- un mot de la liste au début ou à la fin d'une zone en est retiré (« Dr DUPONT » → « DUPONT »).

Les titres n'ont pas de tiret cadratin : ces sections ne déclarent pas de champ (voir catalog.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .catalog import CatalogError, property_key
from .models import DocumentContent, Span

SECTION_TITLE = "organisations a ne jamais pseudonymiser"
WORDS_SECTION_TITLE = "mots a ne jamais pseudonymiser"

_HEADING_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_PROPERTY_RE = re.compile(r"^\s*[-*]\s*\*\*([^*]+?)\*\*\s*:\s*(.+?)\s*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_EMAIL_RE = re.compile(r"^\s*([^@\s]+)@([^@\s]+?)\s*$")


@dataclass(frozen=True)
class Exceptions:
    """Organisations dont le nom, les adresses génériques et le domaine e-mail sont conservés."""

    names: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    generic_mailboxes: tuple[str, ...] = ()
    words: tuple[str, ...] = ()  # mots à ne jamais pseudonymiser (civilités, vocabulaire courant)

    def is_protected_word(self, word: str) -> bool:
        """Vrai si le mot est dans la liste des mots à ne jamais pseudonymiser (casse et accents ignorés)."""
        value = _fold(word)
        return bool(value) and value in {_fold(w) for w in self.words}

    def trim_words(self, text: str) -> tuple[int, int] | None:
        """Bornes [début, fin[ de text sans ses mots protégés de tête et de queue ; None s'il n'en
        reste rien (« Dr DUPONT » → bornes de « DUPONT » ; « DEVIS » → None)."""
        tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]

        def removable(token: tuple[int, int]) -> bool:
            # Mot protégé, éventuellement suivi de son deux-points (« Nom: »), ou ponctuation seule
            # restée en bord de zone après un libellé (« Prénom : CHLOÉ » → « CHLOÉ »).
            word = text[token[0]:token[1]]
            return not any(c.isalnum() for c in word) or self.is_protected_word(word.rstrip(":;"))

        while tokens and removable(tokens[0]):
            tokens.pop(0)
        while tokens and removable(tokens[-1]):
            tokens.pop()
        if not tokens:
            return None
        return tokens[0][0], tokens[-1][1]

    def is_organisation(self, text: str) -> bool:
        """Vrai si le texte est exactement le nom d'une organisation de la liste (casse et accents ignorés)."""
        value = _fold(text)
        return bool(value) and value in {_fold(n) for n in self.names}

    def listed_domain(self, email: str) -> str | None:
        """Domaine de l'adresse s'il appartient à la liste (sous-domaines compris), sinon None."""
        match = _EMAIL_RE.match(email)
        if not match:
            return None
        domain = match.group(2).lower().rstrip(".")
        if any(domain == d or domain.endswith("." + d) for d in self.domains):
            return match.group(2).strip()
        return None

    def keeps_email(self, email: str) -> bool:
        """Vrai pour une adresse générique d'un domaine de la liste (contact@, info@…) : conservée telle quelle."""
        if self.listed_domain(email) is None:
            return False
        local = _EMAIL_RE.match(email).group(1).lower()
        words = [w for w in re.split(r"[.\-_+]", local) if w and not w.isdigit()]
        generic = {g.lower() for g in self.generic_mailboxes}
        return bool(words) and all(w in generic for w in words)

    def with_listed_domain(self, original: str, surrogate: str) -> str:
        """Valeur fictive d'une adresse nominative : partie avant @ fictive, domaine d'origine conservé."""
        domain = self.listed_domain(original)
        if domain is None or "@" not in surrogate:
            return surrogate
        return f"{surrogate.split('@', 1)[0]}@{domain}"


def _split(text: str) -> list[str]:
    """Valeurs d'une puce, séparées par des virgules ou des points-virgules."""
    return [v.strip() for v in re.split(r"[,;]", text) if v.strip()]


def _fold(text: str) -> str:
    """Minuscules sans accents, espaces et ponctuation réduits (comparaison de noms)."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()
    return re.sub(r"[\s\-'’.,]+", " ", folded).strip()


def parse_exceptions(markdown: str) -> Exceptions:
    """Lit la section « Organisations à ne jamais pseudonymiser » (absente : aucune exception).

    Lève CatalogError si un domaine est mal écrit.
    """
    props: dict[str, list[str]] = {}
    words: list[str] = []
    inside = False
    in_words = False
    key = None
    errors: list[str] = []
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            title = property_key(heading.group(1))
            inside = title == SECTION_TITLE
            in_words = title == WORDS_SECTION_TITLE
            key = None
            continue
        if in_words:
            prop = _PROPERTY_RE.match(line)
            if prop:
                key = "mots"
                words.extend(_split(prop.group(2)))
            elif key and line[:1].isspace() and line.strip() and not line.strip().startswith(("-", "*")):
                words.extend(_split(line))  # suite de la puce sur la ligne suivante
            else:
                key = None
            continue
        if not inside:
            continue
        prop = _PROPERTY_RE.match(line)
        if prop:
            key = property_key(prop.group(1))
            props[key] = _split(prop.group(2))
        elif key and line[:1].isspace() and line.strip() and not line.strip().startswith(("-", "*")):
            props[key] += _split(line)  # suite de la puce sur la ligne suivante
        else:
            key = None
            continue
        if key == "domaines":
            for v in _split(prop.group(2) if prop else line):
                if not _DOMAIN_RE.match(v.lstrip("@").lower()):
                    errors.append(f"ligne {lineno} : domaine « {v} » invalide")
    if errors:
        raise CatalogError("Organisations à ne jamais pseudonymiser invalide :\n- " + "\n- ".join(errors))
    return Exceptions(
        names=tuple(props.get("noms", ())),
        domains=tuple(v.lstrip("@").lower() for v in props.get("domaines", ())),
        generic_mailboxes=tuple(props.get("adresses generiques", ())),
        words=tuple(words),
    )


def apply_exceptions(spans: list[Span], content: DocumentContent, exceptions: Exceptions | None) -> list[Span]:
    """Retire les zones qui désignent une organisation de la liste ou une de ses adresses génériques,
    et les mots protégés : zones qui n'en contiennent que, ou mots en tête et en queue de zone."""
    if exceptions is None or not (exceptions.names or exceptions.domains or exceptions.words):
        return spans
    kept = []
    for s in spans:
        text = s.text(content.line(s.line_id))
        if exceptions.is_organisation(text) or exceptions.keeps_email(text):
            continue
        if exceptions.words and s.type != "EMAIL":
            bounds = exceptions.trim_words(text)
            if bounds is None:
                continue
            if bounds != (0, len(text)):
                # Valeur rognée : le jeton se recalcule sur le texte restant (value remise à zéro).
                s = replace(s, start=s.start + bounds[0], end=s.start + bounds[1], value=None)
        kept.append(s)
    return kept
