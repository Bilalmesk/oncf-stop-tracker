"""Pure domain logic: stop duration and display summaries.

No HTTP, no UI, no source-specific parsing. This module is the part that must
stay correct even if the data provider changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import Stop, StopKind
from .timeutil import day_offset, format_time

PLAUSIBLE_MAX_STOP = timedelta(hours=12)


@dataclass(frozen=True)
class StopSummary:
    station_code: str
    station_name: str
    sequence: int
    kind: StopKind
    arrival: datetime | None
    departure: datetime | None
    arrival_day_offset: int
    departure_day_offset: int
    duration_minutes: int | None
    duration_label: str
    status_label: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "station_code": self.station_code,
            "station_name": self.station_name,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "arrival": self.arrival.isoformat() if self.arrival else None,
            "departure": self.departure.isoformat() if self.departure else None,
            "arrival_time": format_time(self.arrival),
            "departure_time": format_time(self.departure),
            "arrival_day_offset": self.arrival_day_offset,
            "departure_day_offset": self.departure_day_offset,
            "duration_minutes": self.duration_minutes,
            "duration_label": self.duration_label,
            "status": self.status_label,
            "warnings": list(self.warnings),
        }


def stop_duration(arrival: datetime | None, departure: datetime | None) -> timedelta | None:
    """``departure - arrival`` using full datetimes, or ``None``.

    A negative result means the source is internally inconsistent; returning
    ``None`` is honest, guessing a day rollover is not.
    """
    if arrival is None or departure is None:
        return None
    delta = departure - arrival
    return None if delta < timedelta(0) else delta


def summarize_stop(stop: Stop, service_date: date) -> StopSummary:
    warnings: list[str] = []

    if stop.arrival is None and stop.departure is not None and stop.kind is not StopKind.ORIGIN:
        warnings.append("arrival time not published by the source")
    if stop.departure is None and stop.arrival is not None and stop.kind is not StopKind.DESTINATION:
        warnings.append("departure time not published by the source")

    delta = stop_duration(stop.arrival, stop.departure)
    minutes = None if delta is None else int(delta.total_seconds() // 60)

    if stop.kind in (StopKind.ORIGIN, StopKind.DESTINATION):
        minutes = None
    elif delta is not None:
        if delta == timedelta(0):
            warnings.append(
                "arrival equals departure: may be a rounding artefact or a "
                "pass-through, not a measured zero-minute stop"
            )
        elif delta < timedelta(minutes=1):
            warnings.append(
                "arrival and departure are less than one minute apart; "
                "the rounded duration is 0 min"
            )
        elif delta > PLAUSIBLE_MAX_STOP:
            warnings.append(
                f"dwell of {minutes} min exceeds {int(PLAUSIBLE_MAX_STOP.total_seconds() // 3600)} h; "
                "check that the source times belong to the same service"
            )
    elif stop.arrival is not None and stop.departure is not None:
        warnings.append("departure is earlier than arrival in the source data")

    return StopSummary(
        station_code=stop.station.code,
        station_name=stop.station.display_name,
        sequence=stop.sequence,
        kind=stop.kind,
        arrival=stop.shown_arrival,
        departure=stop.shown_departure,
        arrival_day_offset=day_offset(service_date, stop.shown_arrival),
        departure_day_offset=day_offset(service_date, stop.shown_departure),
        duration_minutes=minutes,
        duration_label=stop.duration_label,
        status_label=stop.status.label,
        warnings=tuple(warnings),
    )
