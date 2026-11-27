"""Geometry and scheduled-progress tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from oncf.geo import (
    cumulative_distances,
    haversine_m,
    nearest_point_index,
    point_at_distance,
)
from oncf.models import DataType, Journey, Station, Stop
from oncf.timeutil import TZ
from oncf.tracker import ScheduledProgress

EQUATOR = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]


def test_haversine_one_degree_at_equator():
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111_195, rel=0.01)


def test_cumulative_distances_are_monotonic():
    cum = cumulative_distances(EQUATOR)
    assert cum[0] == 0
    assert cum[1] < cum[2]
    assert cum[2] == pytest.approx(222_390, rel=0.01)


def test_point_at_distance_midpoint():
    cum = cumulative_distances(EQUATOR)
    lat, lon = point_at_distance(EQUATOR, cum, cum[1])
    assert lat == pytest.approx(0.0, abs=1e-9)
    assert lon == pytest.approx(1.0, abs=1e-9)


def test_point_at_distance_clamps():
    cum = cumulative_distances(EQUATOR)
    assert point_at_distance(EQUATOR, cum, -5) == EQUATOR[0]
    assert point_at_distance(EQUATOR, cum, 10**9) == EQUATOR[-1]


def test_nearest_point_index():
    assert nearest_point_index(EQUATOR, 0.01, 1.02) == 1
    assert nearest_point_index(EQUATOR, 0.0, 0.0) == 0


# --- scheduled progress ------------------------------------------------

T0 = datetime(2026, 11, 27, 10, 0, tzinfo=TZ)


def make_journey() -> Journey:
    station_a = Station(code="A", name_fr="A", latitude=0.0, longitude=0.0)
    station_b = Station(code="B", name_fr="B", latitude=0.0, longitude=1.0)
    station_c = Station(code="C", name_fr="C", latitude=0.0, longitude=2.0)
    stops = [
        Stop(station_a, 1, arrival=None, departure=T0, status=DataType.SCHEDULED_OFFICIAL, total_stops=3),
        Stop(
            station_b,
            2,
            arrival=T0 + timedelta(hours=1),
            departure=T0 + timedelta(hours=1),
            status=DataType.SCHEDULED_OFFICIAL,
            total_stops=3,
        ),
        Stop(
            station_c,
            3,
            arrival=T0 + timedelta(hours=2),
            departure=None,
            status=DataType.SCHEDULED_OFFICIAL,
            total_stops=3,
        ),
    ]
    return Journey(service_id="X", service_label="X", travel_date=date(2026, 11, 27), stops=stops)


def test_position_before_departure():
    progress = ScheduledProgress(make_journey(), EQUATOR)
    pos = progress.position_at(T0 - timedelta(minutes=5))
    assert pos is not None
    assert pos.status == "not_departed"
    assert (pos.lat, pos.lon) == (0.0, 0.0)


def test_position_halfway_is_interpolated():
    progress = ScheduledProgress(make_journey(), EQUATOR)
    pos = progress.position_at(T0 + timedelta(minutes=30))
    assert pos is not None
    assert pos.status == "running"
    assert pos.lon == pytest.approx(0.5, abs=1e-6)
    assert pos.fraction == pytest.approx(0.25, rel=1e-3)


def test_position_at_arrival():
    progress = ScheduledProgress(make_journey(), EQUATOR)
    pos = progress.position_at(T0 + timedelta(hours=1))
    assert pos is not None
    # Exactly at B; either leg boundary is acceptable as long as it is at B.
    assert pos.lon == pytest.approx(1.0, abs=1e-6)


def test_position_after_arrival():
    progress = ScheduledProgress(make_journey(), EQUATOR)
    pos = progress.position_at(T0 + timedelta(hours=5))
    assert pos is not None
    assert pos.status == "arrived"
    assert (pos.lat, pos.lon) == (0.0, 2.0)


def test_schedule_points_carry_distances():
    progress = ScheduledProgress(make_journey(), EQUATOR)
    points = progress.schedule_points()
    assert len(points) == 3
    assert points[0]["distance_m"] == 0
    assert points[-1]["distance_m"] == pytest.approx(222_390, rel=0.01)


def test_progress_without_shape_falls_back_to_station_coordinates():
    progress = ScheduledProgress(make_journey(), None)
    assert len(progress.points) == 3
    pos = progress.position_at(T0 + timedelta(minutes=90))
    assert pos is not None and pos.lon > 1.0


def test_simplify_polyline_keeps_endpoints_and_reduces_points():
    from oncf.geo import simplify_polyline

    points = [(0.0, 0.0), (0.0, 0.0001), (0.0, 0.5), (0.0, 0.9999), (0.0, 1.0)]
    simplified = simplify_polyline(points, tolerance=0.01)
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]
    assert len(simplified) < len(points)


def test_simplify_polyline_keeps_a_sharp_corner():
    from oncf.geo import simplify_polyline

    points = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    assert simplify_polyline(points, tolerance=0.01) == points
