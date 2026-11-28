"""Normalized domain model for ONCF journeys and stops.

Design rules (from the project brief):
  * never invent a time, station, train number or delay;
  * missing values are ``None`` and are rendered as "Not available";
  * a scheduled timetable and live/actual data are distinct data types;
  * every value carries its provenance.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

TIMEZONE = "Africa/Casablanca"


def search_key(value: str | None) -> str:
    """Fold text for accent-, case- and punctuation-insensitive search."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("’", "'").replace("-", " ")
    return "".join(text.split())


class DataType(str, Enum):
    """How trustworthy / how current a value is."""

    SCHEDULED_OFFICIAL = "scheduled_official"
    SCHEDULED_UNOFFICIAL = "scheduled_unofficial"
    ESTIMATED = "estimated"
    LIVE = "live"
    SAMPLE = "sample"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"

    @property
    def label(self) -> str:
        return {
            DataType.SCHEDULED_OFFICIAL: "Scheduled (official ONCF)",
            DataType.SCHEDULED_UNOFFICIAL: "Scheduled (unofficial GTFS reproduction)",
            DataType.ESTIMATED: "Estimated (real-time source)",
            DataType.LIVE: "Live / actual",
            DataType.SAMPLE: "Offline sample",
            DataType.MIXED: "Mixed sources (see per-stop status)",
            DataType.UNAVAILABLE: "Unavailable",
        }[self]


class StopKind(str, Enum):
    ORIGIN = "origin"
    INTERMEDIATE = "intermediate"
    DESTINATION = "destination"
    PASS_THROUGH = "pass_through"


@dataclass(frozen=True)
class Station:
    """A station as published by ONCF.

    ``code`` is the official ONCF ``codeGare`` and is stable. Names are kept
    in the three languages ONCF itself provides.
    """

    code: str
    name_fr: str
    name_ar: str | None = None
    name_en: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    city_code: str | None = None

    @property
    def display_name(self) -> str:
        return self.name_fr

    def matches(self, query: str) -> bool:
        q = search_key(query.strip())
        if not q:
            return True
        return any(
            q in search_key(value)
            for value in (self.name_fr, self.name_ar, self.name_en, self.code)
        )


@dataclass(frozen=True)
class Provenance:
    """Where a value came from and when it was retrieved."""

    name: str
    url: str | None
    retrieved_at: datetime | None
    data_type: DataType

    def describe(self) -> str:
        when = self.retrieved_at.strftime("%Y-%m-%d %H:%M %Z") if self.retrieved_at else "unknown time"
        return f"{self.name} · {self.data_type.label} · retrieved {when}"


@dataclass
class Stop:
    """One stop of one journey.

    ``arrival``/``departure`` are timezone-aware datetimes, or ``None`` when the
    source does not publish that value. ``None`` never means midnight.
    """

    station: Station
    sequence: int
    arrival: datetime | None = None
    departure: datetime | None = None
    platform: str | None = None
    status: DataType = DataType.SCHEDULED_OFFICIAL
    #: Number of stops in the journey, when known. Used to tell a terminus
    #: (no departure published, by design) from a middle stop with missing data.
    total_stops: int | None = None

    # --- classification -------------------------------------------------
    @property
    def is_terminus(self) -> bool:
        return self.total_stops is not None and self.sequence == self.total_stops

    @property
    def kind(self) -> StopKind:
        if self.arrival is None and self.departure is None:
            return StopKind.PASS_THROUGH
        if self.sequence == 1:
            return StopKind.ORIGIN
        if self.is_terminus:
            return StopKind.DESTINATION
        return StopKind.INTERMEDIATE

    # --- the core calculation ------------------------------------------
    @property
    def duration(self) -> timedelta | None:
        """Scheduled dwell time, or ``None`` when it cannot be computed.

        Uses full datetimes, so a stop crossing midnight is handled by the
        calendar date itself rather than by adding a day on a guess. Termini
        never have a dwell: a train does not "stop for 0 minutes" at its origin
        or destination.
        """
        if self.kind in (StopKind.ORIGIN, StopKind.DESTINATION, StopKind.PASS_THROUGH):
            return None
        if self.arrival is None or self.departure is None:
            return None
        delta = self.departure - self.arrival
        if delta < timedelta(0):
            # Data inconsistency: better to say so than to fabricate a day.
            return None
        return delta

    @property
    def duration_minutes(self) -> int | None:
        d = self.duration
        return None if d is None else int(d.total_seconds() // 60)

    @property
    def duration_label(self) -> str:
        if self.kind is StopKind.ORIGIN:
            return "Departure only"
        if self.kind is StopKind.DESTINATION:
            return "Arrival only"
        if self.kind is StopKind.PASS_THROUGH:
            return "No scheduled stop"
        if self.arrival is None:
            return "Departure only"
        if self.departure is None:
            return "Arrival only"
        minutes = self.duration_minutes
        if minutes is None:
            return "Not available"
        return f"{minutes} min"

    # --- display helpers ------------------------------------------------
    @property
    def shown_arrival(self) -> datetime | None:
        """Arrival as a passenger should read it (blank at the origin)."""
        return None if self.kind is StopKind.ORIGIN else self.arrival

    @property
    def shown_departure(self) -> datetime | None:
        """Departure as a passenger should read it (blank at the destination)."""
        return None if self.kind is StopKind.DESTINATION else self.departure


@dataclass
class Journey:
    """An ordered set of stops for one service on one travel date."""

    service_id: str
    service_label: str
    travel_date: date
    stops: list[Stop] = field(default_factory=list)
    provenance: Provenance | None = None
    timezone: str = TIMEZONE
    notes: list[str] = field(default_factory=list)
    product: str = ""
    product_color: str = ""

    def stop_for(self, station_code: str) -> Stop | None:
        for stop in self.stops:
            if stop.station.code == station_code:
                return stop
        return None

    @property
    def origin(self) -> Stop | None:
        return self.stops[0] if self.stops else None

    @property
    def destination(self) -> Stop | None:
        return self.stops[-1] if self.stops else None
