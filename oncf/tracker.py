"""Scheduled progress along a trip's shape.

This is **not** train tracking. ONCF publishes no vehicle positions, so the
position produced here is an interpolation of the published timetable along the
route geometry. It is labelled ``scheduled_estimate`` everywhere it appears and
must never be presented as the train's real location.

The genuinely live signal in this project is the *passenger's own* GPS position,
which the browser provides with their consent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .geo import cumulative_distances, nearest_point_index, point_at_distance
from .models import Journey, Stop


@dataclass(frozen=True)
class ScheduledPosition:
    lat: float
    lon: float
    fraction: float
    at: datetime
    status: str  # "not_departed" | "running" | "arrived"
    leg_index: int


class ScheduledProgress:
    def __init__(self, journey: Journey, polyline: list[tuple[float, float]] | None = None):
        self.journey = journey
        points = polyline or [
            (s.station.latitude, s.station.longitude)
            for s in journey.stops
            if s.station.latitude is not None and s.station.longitude is not None
        ]
        self.points = points
        self.cumdist = cumulative_distances(points)
        self.stop_distances = self._stop_distances(journey.stops)

    def _stop_distances(self, stops: list[Stop]) -> list[float | None]:
        if not self.points:
            return [None for _ in stops]
        distances: list[float | None] = []
        previous = 0.0
        for stop in stops:
            lat, lon = stop.station.latitude, stop.station.longitude
            if lat is None or lon is None:
                distances.append(None)
                continue
            index = nearest_point_index(self.points, lat, lon)
            distance = max(self.cumdist[index], previous)  # enforce forward motion
            distances.append(distance)
            previous = distance
        return distances

    @property
    def total_distance(self) -> float:
        return self.cumdist[-1] if self.cumdist else 0.0

    def _departure(self, stop: Stop) -> datetime | None:
        return stop.shown_departure or stop.departure or stop.arrival

    def _arrival(self, stop: Stop) -> datetime | None:
        return stop.shown_arrival or stop.arrival or stop.departure

    def position_at(self, moment: datetime) -> ScheduledPosition | None:
        stops = self.journey.stops
        if not self.points or not stops:
            return None

        first_departure = None
        for stop in stops:
            first_departure = self._departure(stop)
            if first_departure is not None:
                break
        last_arrival = None
        for stop in reversed(stops):
            last_arrival = self._arrival(stop)
            if last_arrival is not None:
                break

        if first_departure is None:
            return None
        if moment <= first_departure:
            return ScheduledPosition(*self.points[0], 0.0, moment, "not_departed", 0)
        if last_arrival is not None and moment >= last_arrival:
            return ScheduledPosition(*self.points[-1], 1.0, moment, "arrived", len(stops) - 1)

        for index in range(len(stops) - 1):
            start, end = stops[index], stops[index + 1]
            departure = self._departure(start)
            arrival = self._arrival(end)
            if departure is None or arrival is None or arrival <= departure:
                continue
            if not (departure <= moment <= arrival):
                continue

            start_distance = self.stop_distances[index]
            end_distance = self.stop_distances[index + 1]
            if start_distance is None or end_distance is None:
                return None

            fraction_of_leg = (moment - departure).total_seconds() / (arrival - departure).total_seconds()
            distance = start_distance + (end_distance - start_distance) * fraction_of_leg
            lat, lon = point_at_distance(self.points, self.cumdist, distance)
            total = self.total_distance or 1.0
            return ScheduledPosition(lat, lon, distance / total, moment, "running", index)

        return None

    def schedule_points(self) -> list[dict]:
        """Per-stop distance and time, for the client-side countdown."""
        out = []
        for index, stop in enumerate(self.journey.stops):
            out.append(
                {
                    "sequence": stop.sequence,
                    "station_code": stop.station.code,
                    "distance_m": self.stop_distances[index],
                    "departure": self._departure(stop).isoformat() if self._departure(stop) else None,
                    "arrival": self._arrival(stop).isoformat() if self._arrival(stop) else None,
                }
            )
        return out
