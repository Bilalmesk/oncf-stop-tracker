"""Regression tests for PDFs with two timetables side by side."""
from __future__ import annotations

import pytest

from oncf.config import AUTO_GRID_DIR
from oncf.grid import GridLibrary

pytestmark = pytest.mark.skipif(
    not (AUTO_GRID_DIR / "02-Casa-Kenitra-phase-2.pdf").exists()
    or not (AUTO_GRID_DIR / "03-Casa-Aeroport-phase-2.pdf").exists(),
    reason="auto-current grid PDFs not downloaded",
)


@pytest.fixture(scope="module")
def library() -> GridLibrary:
    return GridLibrary.load_dir(AUTO_GRID_DIR)


def test_side_by_side_tables_are_split_into_separate_trains(library):
    casa_kenitra = next(g for g in library.grids if g.source.name.startswith("02-"))
    airport = next(g for g in library.grids if g.source.name.startswith("03-"))

    assert len(casa_kenitra.sections[0].trips) >= 40
    assert len(airport.sections[0].trips) >= 30
    assert "2" in {trip.train for trip in casa_kenitra.sections[0].trips}
    assert "A6" in {trip.train for trip in airport.sections[0].trips}


def test_right_hand_train_is_verified_at_its_own_time(library):
    match = library.verify("2", "Salé Tabriquet", "05:46")
    assert match.status == "match"
    assert match.printed == "05:46"

    airport = library.verify("A6", "Aéroport", "05:55")
    assert airport.status == "match"
    assert airport.printed == "05:55"


def test_left_hand_train_does_not_inherit_right_hand_times(library):
    mismatch = library.verify("1", "Salé Tabriquet", "05:46")
    assert mismatch.status == "mismatch"
    assert mismatch.printed == "07:45"
