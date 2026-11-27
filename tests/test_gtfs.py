"""Data-layer tests against the bundled GTFS snapshot.

Skipped automatically when the feed has not been downloaded.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from oncf.config import DATA_DIR
from oncf.gtfs import GtfsFeed
from oncf.models import StopKind
from oncf.timeutil import TZ

FEED = DATA_DIR / "orhazal"
pytestmark = pytest.mark.skipif(not (FEED / "stops.txt").exists(), reason="GTFS feed not downloaded")


@pytest.fixture(scope="module")
def feed() -> GtfsFeed:
    return GtfsFeed(FEED)


def test_station_codes_match_official_catalogue(feed):
    assert feed.station("200") is not None
    assert feed.station("380").name_fr.upper().startswith("FES")
    assert feed.station("490").name_fr.upper() == "OUJDA"


def test_arabic_names_are_present(feed):
    assert feed.station("380").name_ar == "فاس"
    assert feed.station("200").name_ar


def _find_service_date(feed: GtfsFeed, train: str) -> date:
    """Pick the next date on which the train actually runs."""
    for offset in range(0, 21):
        candidate = date(2026, 11, 27) + timedelta(days=offset)
        if feed.trips_between("200", "490", candidate, train_number=train):
            return candidate
    pytest.skip(f"train {train} not found in feed calendar")


def test_casa_oujda_train_ma_is_present(feed):
    travel_date = _find_service_date(feed, "MA")
    trips = feed.trips_between("200", "490", travel_date, train_number="MA")
    assert len(trips) == 1
    journey = feed.journey(trips[0].trip_id, travel_date)
    codes = [s.station.code for s in journey.stops]
    assert codes[0] == "200"
    assert codes[-1] == "490"
    assert "380" in codes
    assert len(codes) == 20


def test_fes_stop_duration_from_gtfs_is_31_minutes(feed):
    travel_date = _find_service_date(feed, "MA")
    journey = feed.journey_for_train("MA", travel_date, origin_code="200", destination_code="490")
    fes = journey.stop_for("380")
    assert fes is not None
    assert fes.duration_minutes == 31
    assert fes.arrival is not None and fes.departure is not None
    # Departs Casa Voyageurs at 21:07, so Fès is reached after midnight.
    assert fes.arrival.date() == travel_date + timedelta(days=1)
    assert fes.departure.date() == travel_date + timedelta(days=1)


def test_gtfs_times_are_marked_unofficial(feed):
    travel_date = _find_service_date(feed, "MA")
    journey = feed.journey_for_train("MA", travel_date, origin_code="200", destination_code="490")
    assert journey.provenance is not None
    assert journey.provenance.data_type.value == "scheduled_unofficial"
    assert all(s.status.value == "scheduled_unofficial" for s in journey.stops)


def test_terminus_kinds(feed):
    travel_date = _find_service_date(feed, "MA")
    journey = feed.journey_for_train("MA", travel_date, origin_code="200", destination_code="490")
    assert journey.stops[0].kind is StopKind.ORIGIN
    assert journey.stops[-1].kind is StopKind.DESTINATION
    assert journey.stop_for("380").kind is StopKind.INTERMEDIATE


def test_overnight_ordering_is_monotonic(feed):
    travel_date = _find_service_date(feed, "MA")
    journey = feed.journey_for_train("MA", travel_date, origin_code="200", destination_code="490")
    previous = None
    for stop in journey.stops:
        moment = stop.arrival or stop.departure
        if previous is not None and moment is not None:
            assert moment >= previous, f"non-monotonic at {stop.station.name_fr}"
        if moment is not None:
            previous = moment


def test_second_route_is_supported(feed):
    # Al Boraq Casa Voyageurs -> Tanger Ville.
    for offset in range(0, 14):
        candidate = date(2026, 11, 27) + timedelta(days=offset)
        trips = feed.trips_between("200", "303", candidate)
        if trips:
            journey = feed.journey(trips[0].trip_id, candidate)
            assert len(journey.stops) >= 4
            return
    pytest.skip("no Al Boraq trip found in the sampled window")


# --- city-level (multiple stations) ------------------------------------


def test_trips_between_city_sets(feed):
    travel_date = _find_service_date(feed, "MA")
    rows = feed.trips_between_sets(["191", "200", "206"], ["229", "231"], travel_date)
    trains = {trip.train_number for trip, _, _ in rows}
    assert "MA" in trains
    # Boarding is the first Casablanca call, alighting the first Rabat call.
    for trip, board, alight in rows:
        if trip.train_number == "MA":
            assert feed.station_code_for_stop(board["stop_id"]) == "200"
            assert feed.station_code_for_stop(alight["stop_id"]) in {"229", "231"}
            break


def test_repository_city_search_populates_station_pairs(feed):
    from oncf.repository import Repository
    from oncf.stations import StationCatalog

    catalog = StationCatalog(feed.stations, "gtfs", 0)
    repo = Repository(catalog, official=None, gtfs=feed)
    travel_date = _find_service_date(feed, "MA")

    options = repo.search_between_sets(["191", "200", "206"], ["229", "231"], travel_date)
    ma = next(option for option in options if option.train_number == "MA")
    assert ma.origin_code in {"191", "200", "206"}
    assert ma.destination_code in {"229", "231"}
    assert ma.origin_name and ma.destination_name
    assert ma.data_type.value == "scheduled_unofficial"

    journey = repo.journey_between_sets("MA", ["191", "200", "206"], ["229", "231"], travel_date)
    codes = [stop.station.code for stop in journey.stops]
    assert "200" in codes and "229" in codes


def test_city_search_covers_casa_port_and_oasis(feed):
    """Casablanca has several gares; a city search must not only use Voyageurs."""
    travel_date = _find_service_date(feed, "MA")
    rows = feed.trips_between_sets(["191", "200", "206"], ["229"], travel_date)
    origins = {feed.station_code_for_stop(board["stop_id"]) for _, board, _ in rows}
    assert len(origins) >= 2


def test_network_overview_has_products_and_shapes(feed):
    routes = feed.network()
    assert routes
    assert all(len(route["shape"]) >= 2 for route in routes)
    products = {route["product"] for route in routes}
    assert {"Al Boraq", "Al Atlas"} <= products
    # Simplification must actually reduce the payload.
    assert sum(len(route["shape"]) for route in routes) < 20000
