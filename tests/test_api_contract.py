"""Contract tests pinned to real ONCF responses saved in data/official/.

These run offline: they protect the parser against source-format drift and
document the verified example from the brief (train MA, Casa Voyageurs -> Oujda).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from oncf.api import _build_payload, _parse_legs
from oncf.config import OFFICIAL_DIR
from oncf.models import DataType
from oncf.repository import Repository
from oncf.serialization import journey_from_dict, journey_to_dict
from oncf.serialization import station_to_dict
from oncf.stations import StationCatalog
from oncf.timeutil import TZ

pytestmark = pytest.mark.skipif(
    not (OFFICIAL_DIR / "schedule_200_380.json").exists(),
    reason="captured ONCF responses not present",
)


def load(name: str) -> dict:
    return json.loads((OFFICIAL_DIR / name).read_text(encoding="utf-8"))


def test_station_catalogue_has_official_codes():
    raw = load("cache_stations.json")
    catalog = StationCatalog._parse(raw)
    assert catalog["200"].name_fr == "CASA VOYAGEURS"
    assert catalog["380"].name_fr == "FES"
    assert catalog["490"].name_fr == "OUJDA"
    assert catalog["380"].name_ar == "فاس"
    assert len(catalog) > 100


def test_resolve_prefers_exact_name_over_substring():
    catalog = StationCatalog(StationCatalog._parse(load("cache_stations.json")), "test", 0)

    # "Oujda" is a substring of "AIN-TAOUJDATE"; the exact station must win.
    assert catalog.resolve("Oujda").code == "490"
    assert catalog.resolve("oujda").code == "490"
    assert catalog.resolve("Fes").code == "380"
    assert catalog.resolve("Casa Voyageurs").code == "200"
    assert catalog.resolve("200").code == "200"

    with pytest.raises(KeyError):
        catalog.resolve("ain")


def test_city_catalog_groups_all_stations_of_a_city():
    from oncf.cities import CityCatalog

    stations = StationCatalog._parse(load("cache_stations.json"))
    cities = CityCatalog.from_payload(load("cache_cities.json"), stations)

    casablanca = cities.get("33")
    assert casablanca.name_fr.upper() == "CASABLANCA"
    # Everything the user asked for: Oasis, Port, Voyageurs, plus the halts.
    assert {"191", "200", "206"} <= set(casablanca.station_codes)
    assert cities.resolve("Casablanca").code == "33"
    assert cities.resolve("Rabat").code == "39"
    assert cities.resolve("c:39").code == "39"


def test_casa_fes_leg_lists_train_ma_at_0107():
    legs = _parse_legs(load("schedule_200_380.json")["body"])
    ma = next(leg for leg in legs if leg.primary_train == "MA")
    assert ma.is_direct
    assert ma.departure == datetime(2026, 11, 27, 21, 7, tzinfo=TZ)
    assert ma.arrival == datetime(2026, 11, 28, 1, 7, tzinfo=TZ)


def test_raw_fes_oujda_leg_shows_the_midnight_trap():
    """ONCF answers "what leaves Fès on 27 Nov", i.e. the previous night's train.

    The departure is 01:40 on the 27th, not on the 28th where train MA (which
    left Casa Voyageurs at 21:07 on the 27th) actually calls. A naive lookup
    here produces a dwell of -24 h.
    """
    legs = _parse_legs(load("schedule_380_490.json")["body"])
    ma = next(leg for leg in legs if leg.primary_train == "MA")
    assert ma.departure == datetime(2026, 11, 27, 1, 40, tzinfo=TZ)


class FakeTimetable:
    """Minimal stand-in for the gateway that reproduces the midnight trap.

    Nominal service: Casa Voyageurs 21:07 -> Fès 01:07/01:40 -> Oujda 07:11.
    ``schedule(dep, arr, date)`` returns the train that *departs ``dep`` on that
    calendar date*, exactly like ONCF does.
    """

    last_retrieved_at = None

    ARRIVAL_OFFSET = {"380": 240, "490": 604}   # minutes after 21:07
    DEPARTURE_OFFSET = {"200": 0, "380": 273}   # minutes after 21:07

    def _departure(self, code: str, travel_date):
        if code not in self.DEPARTURE_OFFSET:
            return None
        clock = {
            "200": (21, 7),
            "380": (1, 40),
        }[code]
        return datetime.combine(travel_date, datetime.min.time(), tzinfo=TZ) + timedelta(
            hours=clock[0], minutes=clock[1]
        )

    def schedule(self, dep: str, arr: str, travel_date, force: bool = False):
        from oncf.api import Leg, Segment

        departure = self._departure(dep, travel_date)
        if departure is None or arr not in self.ARRIVAL_OFFSET:
            return []
        arrival = departure + timedelta(minutes=self.ARRIVAL_OFFSET[arr] - self.DEPARTURE_OFFSET[dep])
        segment = Segment(dep, arr, departure, arrival, "MA", "58", "2")
        return [Leg(dep, arr, departure, arrival, [segment])]

    def direct_leg(self, dep, arr, travel_date, train_number):
        for leg in self.schedule(dep, arr, travel_date):
            if leg.is_direct and leg.primary_train == train_number:
                return leg
        return None


def test_repository_resolves_post_midnight_departure_correctly():
    from oncf.repository import Repository
    from oncf.stations import StationCatalog

    raw = load("cache_stations.json")
    catalog = StationCatalog._parse(raw)
    repo = Repository(catalog, official=FakeTimetable(), gtfs=None)

    arrival, departure, notes = repo.official_stop_times(
        "MA", "200", "490", "380", date(2026, 11, 27)
    )
    assert arrival == datetime(2026, 11, 28, 1, 7, tzinfo=TZ)
    assert departure == datetime(2026, 11, 28, 1, 40, tzinfo=TZ)
    assert departure - arrival == timedelta(minutes=33)
    assert notes == []


def test_hydrate_all_stops_verifies_every_pair_and_dwell():
    """Bulk verification keeps the Fès 01:07→01:40 dwell (33 min), not a 24 h error."""
    from oncf.models import Journey, Stop

    catalog = StationCatalog._parse(load("cache_stations.json"))
    repo = Repository(catalog, official=FakeTimetable(), gtfs=None)
    departure = datetime(2026, 11, 27, 21, 7, tzinfo=TZ)
    fes_departure = datetime(2026, 11, 28, 1, 40, tzinfo=TZ)
    journey = Journey(
        service_id="fake:MA",
        service_label="MA",
        travel_date=date(2026, 11, 27),
        stops=[
            Stop(station=catalog.get("200"), sequence=1, departure=departure, total_stops=3),
            Stop(station=catalog.get("380"), sequence=2, departure=fes_departure, total_stops=3),
            Stop(station=catalog.get("490"), sequence=3, total_stops=3),
        ],
    )

    verified = repo.hydrate_all_stops(journey)

    assert verified.stops[0].departure == departure
    assert verified.stops[1].arrival == datetime(2026, 11, 28, 1, 7, tzinfo=TZ)
    assert verified.stops[1].departure == fes_departure
    assert verified.stops[1].duration_minutes == 33
    assert verified.stops[2].arrival == datetime(2026, 11, 28, 7, 11, tzinfo=TZ)
    assert verified.stops[1].status is DataType.SCHEDULED_OFFICIAL
    assert any("2/2 consecutive legs" in note for note in verified.notes)


def test_grid_labels_keep_sale_tabriquet_and_meknes_al_amir_distinct():
    catalog = StationCatalog(StationCatalog._parse(load("cache_stations.json")), "test", 0)
    repo = Repository(catalog, official=None, gtfs=None)

    assert repo._catalog_station_for("Salé Ville").code == "237"
    assert repo._catalog_station_for("Salé Tabriquet").code != "237"
    assert repo._catalog_station_for("Meknès Al Amir").code != "363"


def test_verified_fes_stop_is_33_minutes():
    arrival = next(leg for leg in _parse_legs(load("schedule_200_380.json")["body"]) if leg.primary_train == "MA").arrival
    # The same service departs Fès the following morning.
    departure = arrival.replace(hour=1, minute=40)
    assert departure - arrival == timedelta(minutes=33)


def test_ticket_example_end_to_end():
    """The ticket in the brief: 21:07 Casa Voyageurs, 07:11 Oujda."""
    legs = _parse_legs(load("schedule_200_490.json")["body"])
    ma = next(leg for leg in legs if leg.primary_train == "MA")
    assert (ma.departure.hour, ma.departure.minute) == (21, 7)
    assert (ma.arrival.hour, ma.arrival.minute) == (7, 11)
    assert ma.arrival.date() == date(2026, 11, 28)


def test_payload_contains_every_field_the_gateway_requires():
    payload = _build_payload("200", "380", date(2026, 11, 27))
    required = {
        "codeGareDepart",
        "codeGareArrivee",
        "codeNiveauConfort",
        "dateDepartAller",
        "dateDepartAllerMax",
        "dateDepartRetour",
        "dateDepartRetourMax",
        "isTrainDirect",
        "isPreviousTrainAller",
        "isTarifReduit",
        "adulte",
        "kids",
        "listVoyageur",
        "booking",
        "isEntreprise",
        "token",
        "numeroContract",
        "codeTiers",
        "iTravel",
        "isActive",
    }
    assert required <= payload.keys()
    assert payload["dateDepartAller"].startswith("2026-11-27T")


def test_normalized_json_round_trip():
    raw = load("cache_stations.json")
    catalog = StationCatalog._parse(raw)
    journey = journey_from_dict(
        {
            "service_id": "MA",
            "service_label": "MA",
            "travel_date": "2026-11-27",
            "timezone": "Africa/Casablanca",
            "source": {"name": "test", "data_type": "scheduled_official"},
            "stops": [
                {"station_id": "200", "sequence": 1, "departure": "2026-11-27T21:07:00+01:00"},
                {
                    "station_id": "380",
                    "sequence": 2,
                    "arrival": "2026-11-28T01:07:00+01:00",
                    "departure": "2026-11-28T01:40:00+01:00",
                },
                {"station_id": "490", "sequence": 3, "arrival": "2026-11-28T07:11:00+01:00"},
            ],
        },
        stations=catalog,
    )
    assert journey.stop_for("380").duration_minutes == 33
    payload = journey_to_dict(journey)
    assert payload["stops"][1]["stop_duration_minutes"] == 33
    assert payload["stops"][1]["kind"] == "intermediate"
    assert payload["stops"][0]["kind"] == "origin"
    assert payload["stops"][2]["kind"] == "destination"

