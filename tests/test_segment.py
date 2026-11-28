"""Tests for segment trimming and the full city list."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from oncf.config import OFFICIAL_DIR
from oncf.models import DataType, Journey, Station, Stop
from oncf.repository import trim_to_segment, Repository
from oncf.stations import StationCatalog

pytestmark = pytest.mark.skipif(
    not (OFFICIAL_DIR / "cache_stations.json").exists(), reason="station cache not captured"
)


def make_journey() -> Journey:
    names = [
        ("110", "Marrakech", 18, 35),
        ("191", "Casa Oasis", 21, 14),
        ("200", "Casa Voyageurs", 21, 26),
        ("229", "Rabat Agdal", 22, 28),
        ("231", "Rabat Ville", 22, 33),
        ("380", "Fes", 1, 10),
    ]
    stops = []
    for index, (code, name, hour, minute) in enumerate(names):
        day = 26 if hour > 12 else 27
        moment = datetime(2026, 11, day, hour, minute)
        stops.append(
            Stop(
                Station(code=code, name_fr=name),
                index + 1,
                arrival=None if index == 0 else moment,
                departure=None if index == len(names) - 1 else moment,
                status=DataType.SCHEDULED_UNOFFICIAL,
                total_stops=len(names),
            )
        )
    return Journey("t", "628", date(2026, 11, 26), stops)


def test_trim_keeps_only_the_requested_segment():
    journey = make_journey()
    trimmed = trim_to_segment(journey, ["191", "200"], ["231"])

    assert [s.station.name_fr for s in trimmed.stops] == [
        "Casa Oasis",
        "Casa Voyageurs",
        "Rabat Agdal",
        "Rabat Ville",
    ]
    assert "Marrakech" not in [s.station.name_fr for s in trimmed.stops]
    assert "Fes" not in [s.station.name_fr for s in trimmed.stops]
    assert trimmed.stops[0].kind.value == "origin"
    assert trimmed.stops[-1].kind.value == "destination"
    assert [s.sequence for s in trimmed.stops] == [1, 2, 3, 4]
    assert all(s.total_stops == 4 for s in trimmed.stops)
    assert any("continues beyond" in note for note in trimmed.notes)


def test_trim_rejects_reverse_direction():
    journey = make_journey()
    with pytest.raises(LookupError):
        trim_to_segment(journey, ["231"], ["200"])


def test_trim_rejects_unknown_origin():
    journey = make_journey()
    with pytest.raises(LookupError):
        trim_to_segment(journey, ["999"], ["231"])


def test_trim_noop_when_already_the_segment():
    journey = make_journey()
    trimmed = trim_to_segment(journey, ["110"], ["380"])
    assert len(trimmed.stops) == len(journey.stops)
    assert trimmed.notes == journey.notes


def test_city_catalog_can_list_all_cities_including_stationless():
    from oncf.cities import CityCatalog

    stations = StationCatalog._parse(json.loads((OFFICIAL_DIR / "cache_stations.json").read_text(encoding="utf-8")))
    cities = CityCatalog.from_payload(
        json.loads((OFFICIAL_DIR / "cache_cities.json").read_text(encoding="utf-8")), stations
    )

    usable = cities.search("", limit=500)
    every = cities.search("", limit=500, include_empty=True)
    assert len(every) == 197
    assert len(usable) == 122
    assert len(usable) < len(every)
    assert all(city.station_codes for city in usable)
