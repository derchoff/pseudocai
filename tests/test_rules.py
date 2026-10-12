from pseudo.detection.regex_rules import detect_regex, nir_key
from pseudo.models import DocumentContent, Line, PageContent, Span, Word
from pseudo.pseudonymizer import Pseudonymizer, ValueCipher, normalize
from pseudo.spans import (
    find_fuzzy,
    find_strict,
    merge_spans,
    propagate,
    pseudonymise_line,
)


def make_line(text: str, line_id: str = "p1-l1") -> Line:
    """Ligne de test construite à partir d'un texte (un mot par espace)."""
    return Line.build(line_id, 0, [Word(t, (0, 0, 1, 1)) for t in text.split(" ")])


def make_doc(*texts: str) -> DocumentContent:
    """Document d'une page avec une ligne par texte."""
    lines = [make_line(t, f"p1-l{i + 1}") for i, t in enumerate(texts)]
    return DocumentContent(pages=[PageContent(index=0, source="native", lines=lines)])


# ---------------------------------------------------------------- regex


def test_nir_with_valid_key_is_detected_without_context():
    first13 = "1850375123456"
    key = f"{nir_key(first13):02d}"
    line = make_line(f"Ref 1 85 03 75 123 456 {key} fin")
    spans = [s for s in detect_regex([line]) if s.type == "NIR"]
    assert len(spans) == 1
    assert spans[0].text(line) == f"1 85 03 75 123 456 {key}"


def test_nir_with_wrong_key_requires_context():
    assert not [s for s in detect_regex([make_line("Ref 1 85 03 75 123 456 00")]) if s.type == "NIR"]
    assert [s for s in detect_regex([make_line("N° SS : 1 85 03 75 123 456 00")]) if s.type == "NIR"]


def test_nir_corsica_with_valid_key():
    first13 = "290122A004012"
    key = f"{nir_key(first13):02d}"
    line = make_line(f"Assure 2 90 12 2A 004 012 {key}")
    assert [s.text(line) for s in detect_regex([line]) if s.type == "NIR"] == [f"2 90 12 2A 004 012 {key}"]


def test_siret_is_not_a_nir():
    assert not [s for s in detect_regex([make_line("SIRET 12345678901234")]) if s.type == "NIR"]


def test_phone_email_and_birth_date():
    line = make_line("Né le 12/03/1985 tél +33 6 12 34 56 78 mail jean.dupont@exemple.fr le 01/01/2024")
    found = {(s.type, s.text(line)) for s in detect_regex([line])}
    assert ("DATE_NAISSANCE", "12/03/1985") in found
    assert ("TELEPHONE", "+33 6 12 34 56 78") in found
    assert ("EMAIL", "jean.dupont@exemple.fr") in found
    assert ("DATE_NAISSANCE", "01/01/2024") not in found


def test_date_without_birth_context_is_ignored():
    assert not detect_regex([make_line("Paris, le 12/03/2024")])


# ---------------------------------------------------------------- localisation / fusion


def test_find_strict_tolerates_case_and_spaces():
    assert find_strict("Nom : DUPONT", "dupont") == [(6, 12)]
    assert find_strict("Tel 06 12 34 56 78", "0612345678") == [(4, 18)]


def test_find_fuzzy_expands_to_word_boundaries():
    text = "Patient : DUP0NTEL Jean"
    a, b = find_fuzzy(text, "DUPONTEL", 80)[0]
    assert text[a:b] == "DUP0NTEL"


def test_merge_prefers_llm_and_trims_lower_priority():
    doc = make_doc("M. Jean DUPONT habite ici")
    line = doc.line("p1-l1")
    spans = [
        Span("p1-l1", 8, 14, "NOM", "llm"),
        Span("p1-l1", 3, 14, "PERSONNE", "language"),
    ]
    merged = merge_spans(spans, doc)
    assert [(s.type, s.text(line)) for s in merged] == [("PERSONNE", "Jean"), ("NOM", "DUPONT")]


def test_propagation_finds_other_occurrences_whole_word_only():
    doc = make_doc("Nom : MARTIN", "Signé MARTIN", "MARTINEZ et martin")
    spans = [Span("p1-l1", 6, 12, "NOM", "llm")]
    found = {(s.line_id, s.start) for s in propagate(spans, doc, 3)}
    assert ("p1-l2", 6) in found
    assert ("p1-l3", 12) in found
    assert ("p1-l3", 0) not in found


def test_pseudo_line_maps_offsets_back_to_original():
    line = make_line("Nom DUPONTER fin")
    span = Span(line.id, 4, 10, "NOM", "llm", token="[NOM_X]")
    pl = pseudonymise_line(line, [span])
    assert pl.text == "Nom [NOM_X]ER fin"
    a = pl.text.index("ER")
    assert pl.to_original(a, a + 2) == (10, 12)
    assert pl.to_original(0, 3) == (0, 3)


# ---------------------------------------------------------------- jetons / chiffrement


def test_tokens_are_deterministic_and_normalized():
    p = Pseudonymizer(b"k" * 32, token_length=10)
    assert p.token("TELEPHONE", "+33 6 12 34 56 78") == p.token("TELEPHONE", "06.12.34.56.78")
    assert p.token("NOM", "Élodie") == p.token("NOM", "ELODIE")
    assert p.token("DATE_NAISSANCE", "12 mars 1985") == p.token("DATE_NAISSANCE", "12/03/1985")
    assert p.token("NOM", "DUPONT") != p.token("PRENOM", "DUPONT")
    assert p.token("NOM", "DUPONT").startswith("[NOM_") and len(p.token("NOM", "DUPONT")) == len("[NOM_]") + 10


def test_normalize_nir_and_email():
    assert normalize("alphanumerique", "1 85 03 2a 123 456 78") == "185032A12345678"
    assert normalize("email", "Jean.Dupont @Exemple.fr") == "jean.dupont@exemple.fr"


def test_cipher_roundtrip_and_aad():
    c = ValueCipher(b"x" * 32)
    blob = c.encrypt("[NOM_A]", "DUPONT")
    assert c.decrypt("[NOM_A]", blob) == "DUPONT"
    try:
        c.decrypt("[NOM_B]", blob)
    except Exception:
        pass
    else:
        raise AssertionError("le jeton doit être lié au chiffré (AAD)")
