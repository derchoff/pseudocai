import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def _jetons_par_defaut(monkeypatch):
    """Les tests historiques vérifient des jetons dans les sorties ; le défaut du service est
    « substitution ». Les tests des valeurs fictives repassent explicitement en substitution."""
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "jetons")

