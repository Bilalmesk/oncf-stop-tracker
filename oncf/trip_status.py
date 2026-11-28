"""Timetable-based trip status.

ONCF publishes no train positions. This module answers "where should the train
be right now?" by interpolating the published timetable along the route shape.
Every response is labelled as an estimate and must never be presented as GPS or
telemetry.

The estimator also accepts a delay offset. A positive delay means the train is
running late: at wall-clock time ``now`` its scheduled progress corresponds to
``now - delay``. The original scheduled times are still reported unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .models import Journey, Stop
from .tracker import ScheduledProgress

POSITION_NOTE = (
    "Scheduled estimate from the timetable; not GPS. "
    "ONCF does not publish train positions."
)


def _departure(stop: Stop) -> datetime | None:
    return stop.shown_departure or stop.departure or stop.arrival


def _arrival(stop: Stop) -> datetime | None:
    return stop.shown_arrival or stop.arrival or stop.departure


def _station(stop: Stop) -> dict:
    return {
        "code": stop.station.code,
        "name": stop.station.name_fr,
        "lat": stop.station.latitude,
        "lon": stop.station.longitude,
    }


def _leg(start: Stop, end: Stop) -> dict:
    return {
        "from": _station(start),
        "to": _station(end),
        "departure": _departure(start).isoformat() if _departure(start) else None,
        "arrival": _arrival(end).isoformat() if _arrival(end) else None,
    }


def _fraction_at(progress: ScheduledProgress, index: int) -> float | None:
    distances = progress.stop_distances
    if index < 0 or index >= len(distances):
        return None
    distance = distances[index]
    total = progress.total_distance
    if distance is None or not total:
        return None
    return max(0.0, min(1.0, distance / total))


def estimate_trip_status(
    journey: Journey,
    shape: list[tuple[float, float]] | None,
    moment: datetime,
    *,
    delay_minutes: int = 0,
    delay_source: str = "none",
) -> dict:
    """Return the estimated status of ``journey`` at ``moment``.

    ``delay_minutes`` shifts the schedule reference; it does not rewrite the
    published times. ``delay_source`` is one of ``none``, ``live_board`` or
    ``community`` and is passed through for the UI.
    """
    stops = journey.stops
    delay = max(0, int(delay_minutes or 0))
    effective = moment - timedelta(minutes=delay)
    first_departure = next((_departure(stop) for stop in stops if _departure(stop)), None)
    last_arrival = next(
        (_arrival(stop) for stop in reversed(stops) if _arrival(stop)), None
    )

    base = {
        "as_of": moment.isoformat(),
        "delay_minutes": delay,
        "delay_source": delay_source if delay else "none",
        "scheduled_departure": first_departure.isoformat() if first_departure else None,
        "scheduled_arrival": last_arrival.isoformat() if last_arrival else None,
        "position_note": POSITION_NOTE,
        "station": None,
        "next_station": None,
        "leg": None,
        "fraction": None,
        "lat": None,
        "lon": None,
    }

    if not stops:
        return {
            **base,
            "status": "unknown",
            "status_label": "No timetable for this trip",
        }

    progress = ScheduledProgress(journey, shape or [])

    if first_departure is None:
        return {
            **base,
            "status": "unknown",
            "status_label": "No scheduled departure",
        }

    if effective <= first_departure:
        return {
            **base,
            "status": "not_departed",
            "status_label": f"Not departed — {stops[0].station.name_fr}",
            "station": _station(stops[0]),
            "fraction": 0.0,
        }

    if last_arrival is not None and effective >= last_arrival:
        return {
            **base,
            "status": "arrived",
            "status_label": f"Arrived at {stops[-1].station.name_fr}",
            "station": _station(stops[-1]),
            "fraction": 1.0,
        }

    # A stop is "at station" between its arrival and its departure. This also
    # covers a long dwell where the next leg has not started yet.
    for index, stop in enumerate(stops):
        arrival = _arrival(stop)
        departure = _departure(stop)
        if arrival is None or departure is None or departure <= arrival:
            continue
        if arrival <= effective < departure:
            return {
                **base,
                "status": "at_station",
                "status_label": f"At {stop.station.name_fr}",
                "station": _station(stop),
                "next_station": _station(stops[index + 1]) if index + 1 < len(stops) else None,
                "fraction": _fraction_at(progress, index),
                "lat": stop.station.latitude,
                "lon": stop.station.longitude,
            }

    # Running between two consecutive stops. Use the timetable for the status
    # even when no shape is available; coordinates are then simply null.
    for index in range(len(stops) - 1):
        start, end = stops[index], stops[index + 1]
        departure, arrival = _departure(start), _arrival(end)
        if departure is None or arrival is None or arrival <= departure:
            continue
        if not (departure <= effective <= arrival):
            continue
        position = progress.position_at(effective) if shape else None
        return {
            **base,
            "status": "running",
            "status_label": (
                f"En route: {start.station.name_fr} → {end.station.name_fr}"
            ),
            "leg": _leg(start, end),
            "next_station": _station(end),
            "fraction": position.fraction if position else None,
            "lat": position.lat if position else None,
            "lon": position.lon if position else None,
        }

    return {
        **base,
        "status": "unknown",
        "status_label": "No position for this time",
    }
