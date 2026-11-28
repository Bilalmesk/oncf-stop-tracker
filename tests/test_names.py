"""Tests for display-only GTFS name cleanup."""
from __future__ import annotations

from oncf.names import clean_route_name, clean_station_name


def test_known_station_typos_are_fixed():
    assert clean_station_name("Aeroport Med V") == "Aéroport Mohammed V"
    assert clean_station_name("Ain-taoujdate") == "Aïn Taoujdate"
    assert clean_station_name("Beni-oukil") == "Beni Oukil"
    assert clean_station_name("Sebaa-aioun") == "Sebaa Aioun"
    assert clean_station_name("Facultes") == "Facultés"


def test_common_unaccented_names_are_displayed_with_accents():
    assert clean_station_name("Fes") == "Fès"
    assert clean_station_name("Kenitra") == "Kénitra"
    assert clean_station_name("Meknes") == "Meknès"
    assert clean_station_name("Sale") == "Salé"


def test_station_name_is_unchanged_when_unknown():
    assert clean_station_name("Casa Voyageurs") == "Casa Voyageurs"
    assert clean_station_name("  ") == ""


def test_supratours_suffixes_are_removed_from_route_names():
    assert clean_route_name("Marrakech - Agadir (suprat.)") == "Marrakech - Agadir"
    assert clean_route_name("Marrakech - Inezgane (suprat)") == "Marrakech - Inezgane"
    assert clean_route_name("Tanger Ville - Fnideq (supra.)") == "Tanger Ville - Fnideq"
    assert (
        clean_route_name("Safi (suprat.) - Inezgane (suprat)")
        == "Safi - Inezgane"
    )


def test_supratours_suffix_is_removed_from_products():
    assert clean_route_name("Marrakech - Merzouga (SUPRAT.)") == "Marrakech - Merzouga"
