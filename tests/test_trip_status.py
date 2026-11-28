"""Tests for the timetable-based trip-status estimator."""
from __future__ import annotations

from datetime import datetime, timedelta

from oncf.models import DataType, Journey, Station, Stop
from oncf.timeutil import TZ
from oncf.trip_status import estimate_trip_status

T0 = datetime(2026, 11, 27, 10, 0, tzinfo=TZ)
SHAPE = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]


def make_journey() -> Journey:
    a = Station(code="A", name_fr="Station A", latitude=0.0, longitude=0.0)
    b = Station(code="B", name_fr="Station B", latitude=0.0, longitude=1.0)
    c = Station(code="C", name_fr="Station C", latitude=0.0, longitude=2.0)
    stops = [
        Stop(a, 1, departure=T0, status=DataType.SCHEDULED_OFFICIAL, total_stops=3),
        Stop(
            b,
            2,
            arrival=T0 + timedelta(hours=1),
            departure=T0 + timedelta(hours=1, minutes=10),
            status=DataType.SCHEDULED_OFFICIAL,
            total_stops=3,
        ),
        Stop(
            c,
            3,
            arrival=T0 + timedelta(hours=2),
            status=DataType.SCHEDULED_OFFICIAL,
            total_stops=3,
        ),
    ]
    return Journey("trip", "TEST", T0.date(), stops)


def test_before_departure_reports_not_departed_at_origin():
    moment = T0 - timedelta(minutes=5)
    status = estimate_trip_status(make_journey(), SHAPE, moment)

    assert status["status"] == "not_departed"
    assert status["station"]["code"] == "A"
    assert status["fraction"] == 0.0
    assert "Not departed" in status["status_label"]
    assert status["as_of"] == moment.isoformat()
    assert status["scheduled_departure"] == T0.isoformat()
    assert status["delay_source"] == "none"
    assert "not GPS" in status["position_note"]


def test_dwell_reports_at_station():
    status = estimate_trip_status(
        make_journey(), SHAPE, T0 + timedelta(hours=1, minutes=5)
    )

    assert status["status"] == "at_station"
    assert status["station"]["code"] == "B"
    assert status["next_station"]["code"] == "C"
    assert status["fraction"] > 0
    assert "Station B" in status["status_label"]


def test_between_stations_reports_running_with_leg():
    status = estimate_trip_status(make_journey(), SHAPE, T0 + timedelta(minutes=30))

    assert status["status"] == "running"
    assert status["leg"]["from"]["code"] == "A"
    assert status["leg"]["to"]["code"] == "B"
    assert status["next_station"]["code"] == "B"
    assert 0 < status["fraction"] < 1
    assert status["lat"] is not None
    assert status["lon"] is not None
    assert "Station A" in status["status_label"]
    assert "Station B" in status["status_label"]


def test_after_arrival_reports_arrived_at_destination():
    status = estimate_trip_status(make_journey(), SHAPE, T0 + timedelta(hours=2))

    assert status["status"] == "arrived"
    assert status["station"]["code"] == "C"
    assert status["fraction"] == 1.0
    assert "Arrived" in status["status_label"]
    assert status["scheduled_arrival"] == (T0 + timedelta(hours=2)).isoformat()


def test_delay_shifts_the_schedule_reference():
    status = estimate_trip_status(
        make_journey(),
        SHAPE,
        T0 + timedelta(minutes=30),
        delay_minutes=30,
        delay_source="community",
    )

    assert status["status"] == "not_departed"
    assert status["delay_minutes"] == 30
    assert status["delay_source"] == "community"
    assert status["scheduled_departure"] == T0.isoformat()


def test_missing_shape_still_reports_time_status_without_coordinates():
    status = estimate_trip_status(
        make_journey(), [], T0 + timedelta(minutes=30)
    )

    assert status["status"] == "running"
    assert status["lat"] is None
    assert status["lon"] is None
    assert status["fraction"] is None
    assert "not GPS" in status["position_note"]
