"""Accent- and punctuation-insensitive station/city search."""
from __future__ import annotations

from oncf.cities import City, CityCatalog
from oncf.models import Station, search_key
from oncf.stations import StationCatalog


def station_catalog(*stations: Station) -> StationCatalog:
    return StationCatalog({station.code: station for station in stations}, "test", 0.0)


def city_catalog(*cities: City) -> CityCatalog:
    return CityCatalog({city.code: city for city in cities})


def test_search_key_folds_accents_and_separators():
    assert search_key("Fès") == "fes"
    assert search_key("TANGER-VILLE") == "tangerville"
    assert search_key("Aïn Sebaâ") == "ainsebaa"
    assert search_key("الدار البيضاء") == search_key("الدارالبيضاء")
    assert search_key("  ") == ""


def test_station_matches_accented_and_plain_queries():
    station = Station(code="380", name_fr="FÈS", name_ar="فاس", name_en="FES")
    assert station.matches("fès")
    assert station.matches("Fes")
    assert station.matches("فاس")
    assert station.matches("380")


def test_station_search_and_resolve_ignore_accents():
    fes = Station(code="380", name_fr="FÈS", name_ar="فاس", name_en="FES")
    casa = Station(code="200", name_fr="CASA VOYAGEURS", name_en="CASABLANCA")
    catalog = station_catalog(fes, casa)

    assert [s.code for s in catalog.search("fès")] == ["380"]
    assert [s.code for s in catalog.search("Fes")] == ["380"]
    assert [s.code for s in catalog.search("casablanca")] == ["200"]
    assert catalog.resolve("fès").code == "380"


def test_city_search_and_resolve_ignore_accents():
    fes = City(code="86", name_fr="Fès", name_ar="فاس", station_codes=("380",))
    tangier = City(code="97", name_fr="Tanger", name_ar="طنجة", station_codes=("350",))
    casablanca = City(
        code="33",
        name_fr="Casablanca",
        name_ar="الدارالبيضاء",
        station_codes=("200",),
    )
    catalog = city_catalog(fes, tangier, casablanca)

    assert [c.code for c in catalog.search("fès")] == ["86"]
    assert [c.code for c in catalog.search("Fes")] == ["86"]
    assert [c.code for c in catalog.search("طنجة")] == ["97"]
    assert [c.code for c in catalog.search("الدار البيضاء")] == ["33"]
    assert catalog.resolve("fès").code == "86"
