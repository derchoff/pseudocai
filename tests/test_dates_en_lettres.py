"""Dates de naissance écrites en toutes lettres (actes de naissance et de décès)."""

import pytest

from pseudo.config import Settings
from pseudo.datewords import int_to_words, parse_letter_date, words_to_int
from pseudo.models import DocumentContent, Entity, Line, PageContent, Word
from pseudo.pipeline import Services, run_pipeline, PipelineInput
from pseudo.pseudonymizer import Pseudonymizer, ValueCipher, normalize
from pseudo.surrogates import SurrogateFactory


@pytest.mark.parametrize("n", [1, 16, 17, 21, 71, 77, 80, 81, 91, 99, 100, 180, 1900, 1952, 1985, 1999, 2000, 2024])
def test_numbers_round_trip(n):
    assert words_to_int(int_to_words(n)) == n
    assert words_to_int(int_to_words(n, mil=True, year=True)) == n


def test_numbers_spelling():
    assert int_to_words(1952, mil=True, year=True) == "mil neuf cent cinquante-deux"
    assert int_to_words(1980, mil=True, year=True) == "mil neuf cent quatre-vingt"
    assert int_to_words(80) == "quatre-vingts"
    assert int_to_words(71) == "soixante et onze"
    assert words_to_int("dix-neuf cent cinquante-deux") == 1952  # forme ancienne des actes


@pytest.mark.parametrize("value, iso", [
    ("vingt-trois mars mil neuf cent cinquante-deux", "1952-03-23"),
    ("premier janvier deux mille", "2000-01-01"),
    ("VINGT-TROIS MARS MIL NEUF CENT CINQUANTE-DEUX", "1952-03-23"),
    ("23 mars mil neuf cent cinquante-deux", "1952-03-23"),
    ("dix-neuf février mille neuf cent quatre-vingt-dix-sept", "1997-02-19"),
    ("trente et un decembre mil neuf cent soixante et onze", "1971-12-31"),
])
def test_letter_dates_normalize_like_digit_dates(value, iso):
    assert normalize("date", value) == iso


def test_letter_and_digit_dates_give_the_same_token():
    p = Pseudonymizer(b"k" * 32)
    assert p.token("DATE_NAISSANCE", "vingt-trois mars mil neuf cent cinquante-deux", "date") == \
        p.token("DATE_NAISSANCE", "23/03/1952", "date")


def test_not_a_letter_date():
    assert parse_letter_date("12 mars 1985") is None  # tout en chiffres : traité ailleurs
    assert parse_letter_date("vingt-trois heures") is None


def _surrogate(value):
    factory = SurrogateFactory(b"k" * 32, current_year=2026)
    return factory.unique("date_naissance", value, key=f"D|{normalize('date', value)}", forbidden={normalize("date", value)},
                          normalize=lambda v: normalize("date", v))


def test_letter_surrogate_is_a_letter_date_in_the_same_style():
    fake = _surrogate("vingt-trois mars mil neuf cent cinquante-deux")
    parsed = parse_letter_date(fake)
    assert parsed is not None and parsed.day_in_words and parsed.year_in_words
    assert not any(c.isdigit() for c in fake)
    assert normalize("date", fake) != "1952-03-23"
    upper = _surrogate("VINGT-TROIS MARS MIL NEUF CENT CINQUANTE-DEUX")
    assert upper == upper.upper()
    mixed = parse_letter_date(_surrogate("23 mars mil neuf cent cinquante-deux"))
    assert mixed is not None and not mixed.day_in_words and mixed.year_in_words


class _Detector:
    """Date en lettres coupée sur deux lignes : deux fragments du même group."""
    prompt_sha256 = "fake"

    def detect(self, lines):
        return [Entity("p1-l1", "vingt-trois mars mil neuf", "DATE_NAISSANCE", group="d1"),
                Entity("p1-l2", "cent cinquante-deux", "DATE_NAISSANCE", group="d1")]


def test_split_letter_date_gets_one_surrogate_spread_over_lines(monkeypatch):
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    texts = ["Le vingt-trois mars mil neuf", "cent cinquante-deux, à dix heures, est né"]
    lines = [Line.build(f"p1-l{i}", 0, [Word(w, (0, 0, 1, 1)) for w in t.split()]) for i, t in enumerate(texts, 1)]
    content = DocumentContent(pages=[PageContent(index=0, source="native", lines=lines)])
    svc = Services(detector=_Detector(), pseudonymizer=Pseudonymizer(b"k" * 32), cipher=ValueCipher(b"e" * 32),
                   surrogates=SurrogateFactory(b"k" * 32, current_year=2026))
    result = run_pipeline(PipelineInput(pdf=b"", content=content), Settings.from_env(), svc)
    assert result.status == "ok", result.reasons
    first, second = sorted(result.spans, key=lambda s: s.line_id)
    joined = f"{first.surrogate} {second.surrogate}"
    assert parse_letter_date(joined) is not None  # une seule date fictive, lisible sur les deux lignes
    assert normalize("date", joined) != "1952-03-23"
    assert first.token == second.token
