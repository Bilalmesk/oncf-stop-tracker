"""Serialize/normalize journeys to a stable JSON shape (brief section 8)."""
from __future__ import annotations

from datetime import date, datetime

from .models import DataType, Journey, Provenance, Station, Stop
from .timeutil import localize


def station_to_dict(station: Station) -> dict:
    return {
        "station_id": station.code,
        "name": {"fr": station.name_fr, "ar": station.name_ar, "en": station.name_en},
        "lat": station.latitude,
        "lon": station.longitude,
    }


def stop_to_dict(stop: Stop, service_date: date) -> dict:
    from .calculator import summarize_stop

    summary = summarize_stop(stop, service_date)
    return {
        "station_id": stop.station.code,
        "station_name": {"fr": stop.station.name_fr, "ar": stop.station.name_ar, "en": stop.station.name_en},
        "sequence": stop.sequence,
        "kind": stop.kind.value,
        "arrival": stop.shown_arrival.isoformat() if stop.shown_arrival else None,
        "departure": stop.shown_departure.isoformat() if stop.shown_departure else None,
        "stop_duration_minutes": summary.duration_minutes,
        "platform": stop.platform,
        "status": stop.status.value,
        "warnings": list(summary.warnings),
    }


def journey_to_dict(journey: Journey) -> dict:
    provenance = journey.provenance
    return {
        "service_id": journey.service_id,
        "service_label": journey.service_label,
        "travel_date": journey.travel_date.isoformat(),
        "timezone": journey.timezone,
        "origin_station_id": journey.origin.station.code if journey.origin else None,
        "destination_station_id": journey.destination.station.code if journey.destination else None,
        "source": {
            "name": provenance.name if provenance else None,
            "url": provenance.url if provenance else None,
            "retrieved_at": provenance.retrieved_at.isoformat() if provenance and provenance.retrieved_at else None,
            "data_type": provenance.data_type.value if provenance else DataType.UNAVAILABLE.value,
        },
        "notes": journey.notes,
        "stops": [stop_to_dict(stop, journey.travel_date) for stop in journey.stops],
    }


def journey_from_dict(payload: dict, stations: dict[str, Station] | None = None) -> Journey:
    """Load a normalized journey file (used for offline samples and tests)."""
    source = payload.get("source") or {}
    retrieved = source.get("retrieved_at")
    provenance = Provenance(
        name=source.get("name") or "unknown",
        url=source.get("url"),
        retrieved_at=datetime.fromisoformat(retrieved) if retrieved else None,
        data_type=DataType(source.get("data_type") or DataType.SAMPLE.value),
    )
    stops: list[Stop] = []
    for item in payload.get("stops", []):
        code = str(item.get("station_id") or item.get("station_code") or "")
        names = item.get("station_name") or {}
        station = (stations or {}).get(code) or Station(
            code=code,
            name_fr=names.get("fr") or code,
            name_ar=names.get("ar"),
            name_en=names.get("en"),
        )
        stops.append(
            Stop(
                station=station,
                sequence=int(item.get("sequence") or len(stops) + 1),
                arrival=localize(datetime.fromisoformat(item["arrival"])) if item.get("arrival") else None,
                departure=localize(datetime.fromisoformat(item["departure"])) if item.get("departure") else None,
                platform=item.get("platform"),
                status=DataType(item.get("status") or provenance.data_type.value),
            )
        )
    for stop in stops:
        stop.total_stops = len(stops)
    return Journey(
        service_id=str(payload.get("service_id") or ""),
        service_label=str(payload.get("service_label") or ""),
        travel_date=date.fromisoformat(payload["travel_date"]),
        stops=stops,
        provenance=provenance,
        timezone=payload.get("timezone") or "Africa/Casablanca",
        notes=list(payload.get("notes") or []),
    )
