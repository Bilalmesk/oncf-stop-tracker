"""Tests for the official grid-PDF parser.

The PDFs are ONCF's published timetables. They are optional in a checkout, so
the suite skips when they are absent.
"""
from __future__ import annotations

from datetime import date

import pytest

from oncf.config import GRID_DIR
from oncf.grid import (
    GridLibrary,
    find_train,
    find_trip,
    normalize_name,
    parse_grid_pdf,
    station_matches,
)

PDF_DIR = GRID_DIR
pytestmark = pytest.mark.skipif(
    not (PDF_DIR / "tanger-fes-oujda.pdf").exists(), reason="official grid PDFs not downloaded"
)


@pytest.fixture(scope="module")
def tanger() -> GridLibrary:
    return GridLibrary.load_dir(PDF_DIR)


def test_effective_date_is_parsed():
    grid = parse_grid_pdf(PDF_DIR / "tanger-fes-oujda.pdf")
    assert grid.effective == date(2026, 9, 14)
    assert "Tanger" in grid.title


def test_train_rows_are_reconstructed(tanger):
    grid = parse_grid_pdf(PDF_DIR / "tanger-fes-oujda.pdf")
    trip = find_trip(grid, "301")
    assert trip is not None
    assert trip.times["Tanger Ville"] == "07:40"
    assert trip.times["Oujda"] == "18:50"

    trip303 = find_trip(grid, "303")
    assert trip303 is not None
    assert trip303.times["Tanger Ville"] == "11:50"
    assert trip303.times["Oujda"] == "22:23"


def test_identify_train_by_station_and_time(tanger):
    matches = tanger.identify("Oujda", "18:50")
    assert [(m.train, m.printed) for m in matches] == [("301", "18:50")]

    # The 2025 timetable printed 18:43; the current grid must not match it.
    assert tanger.identify("Oujda", "18:43") == []


def test_city_name_matches_station_label(tanger):
    # The grid says "Tanger Ville"; users and the catalogue say "Tanger".
    matches = tanger.identify("Tanger", "07:40")
    assert matches and matches[0].train == "301"

    # Accents must not matter.
    assert tanger.identify("Fes", "12:55")[0].train == "301"


def test_verify_match_and_mismatch(tanger):
    assert tanger.verify("301", "Oujda", "18:50").status == "match"
    mismatch = tanger.verify("301", "Oujda", "18:43")
    assert mismatch.status == "mismatch"
    assert mismatch.printed == "18:50"
    assert tanger.verify("999", "Oujda", "18:50").status == "no_coverage"


def test_all_five_sheets_parse(tanger):
    described = {item["file"]: item for item in tanger.describe()}
    assert {
        "tanger-fes-oujda.pdf",
        "casa-oriental.pdf",
        "marrakech-fes.pdf",
        "meknes-fes.pdf",
        "taza-fes.pdf",
        "casa-settat.pdf",
        "casa-port-el-jadida.pdf",
        "casa-port-aeroport-med-v.pdf",
    } <= set(described)
    assert described["tanger-fes-oujda.pdf"]["effective"] == "2026-09-14"
    # Al Boraq's dual Départ/Arrivée layout is deliberately not parsed yet.
    assert len(described) >= 12


def test_lettered_train_numbers():
    airport = parse_grid_pdf(PDF_DIR / "casa-port-aeroport-med-v.pdf")
    assert any(trip.train.startswith("A") for trip in airport.trains())

    tanger_marrakech = parse_grid_pdf(PDF_DIR / "marrakech-kenitra-tanger.pdf")
    assert {trip.train for trip in tanger_marrakech.trains()} >= {"MT", "TM"}


def test_footnote_star_on_train_numbers(tanger):
    # Meknès–Fès prints "*171"; the number must still be readable.
    assert any(m.train == "171" for m in tanger.identify("Meknes", "07:25"))


def test_station_matching_rules():
    assert station_matches("Tanger Ville", "Tanger")
    assert station_matches("Salé Ville", "Sale")
    assert station_matches("Fès", "Fes")
    assert station_matches("Al Akba L'hamra", "L'hamra")
    assert not station_matches("Meknès Al Amir", "Fes")
    assert normalize_name("Fès") == "fes"


def test_find_train_rejects_invalid_clock(tanger):
    grid = parse_grid_pdf(PDF_DIR / "tanger-fes-oujda.pdf")
    assert find_train(grid, "Oujda", "not-a-time") == []

