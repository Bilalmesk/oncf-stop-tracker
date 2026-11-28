"""Read-only access to the community ONCF GTFS feed.

Why GTFS at all? The official gateway only answers between the two stations you
ask about, so it cannot tell you the *ordered* list of stations a train serves.
GTFS can, and it is licensed ODbL rather than being an ONCF reproduction.

Important honesty note: the GTFS feed was built from ONCF's published grilles
horaires, which print **one time per intermediate station**. Its arrival /
departure split at intermediate stops is therefore inferred, and differs from
ONCF's own gateway by a few minutes (e.g. Fès on train MA: GTFS 01:10/01:41 vs
official 01:07/01:40). Treat GTFS times as an unofficial schedule and prefer
``OfficialOncfClient`` when an authoritative value is required.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .geo import haversine_m
from .models import DataType, Journey, Provenance, Station, Stop
from .names import clean_route_name, clean_station_name
from .timeutil import TZ, gtfs_time_to_datetime

WEEKDAY_FIELDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class TripInfo:
    trip_id: str
    route_id: str
    service_id: str
    train_number: str
    headsign: str
    direction_id: str | None
    shape_id: str | None = None


class GtfsFeed:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._stop_rows = self._read("stops.txt")
        self._trip_rows = self._read("trips.txt")
        self._stop_time_rows = self._read("stop_times.txt")
        self._route_rows = self._read("routes.txt")
        self._calendar_rows = self._read("calendar.txt")
        self._calendar_dates = self._read_optional("calendar_dates.txt")
        self._translations = self._load_translations()
        self._feed_info = (self._read_optional("feed_info.txt") or [{}])[0]
        self._stop_coords: dict[str, tuple[float, float]] = {}
        for row in self._stop_rows:
            try:
                self._stop_coords[row["stop_id"]] = (float(row["stop_lat"]), float(row["stop_lon"]))
            except (KeyError, ValueError):
                continue

        self.stations: dict[str, Station] = {}
        self._stop_to_station: dict[str, str] = {}
        self._build_stations()
        self._times_by_trip: dict[str, list[dict]] = defaultdict(list)
        for row in self._stop_time_rows:
            self._times_by_trip[row["trip_id"]].append(row)
        for rows in self._times_by_trip.values():
            rows.sort(key=lambda r: int(r["stop_sequence"]))
        self._trips_by_id = {row["trip_id"]: self._to_trip(row) for row in self._trip_rows}
        self._plausible_cache: dict[str, bool] = {}

    # --- loading --------------------------------------------------------
    def _read(self, name: str) -> list[dict]:
        path = self.directory / name
        if not path.exists():
            raise FileNotFoundError(f"GTFS file missing: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _read_optional(self, name: str) -> list[dict]:
        try:
            return self._read(name)
        except FileNotFoundError:
            return []

    def _load_translations(self) -> dict[tuple[str, str], dict[str, str]]:
        out: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        for row in self._read_optional("translations.txt"):
            if row.get("table_name") == "stops" and row.get("field_name") == "stop_name":
                out[row["record_id"]][row["language"]] = row.get("translation", "")
        return out

    def _build_stations(self) -> None:
        parents = {row["stop_id"]: row for row in self._stop_rows if row.get("location_type") == "1"}
        for row in self._stop_rows:
            stop_id = row["stop_id"]
            parent = row.get("parent_station") or ""
            station_id = parent or (stop_id if stop_id in parents else "")
            self._stop_to_station[stop_id] = station_id or stop_id
            if station_id and station_id in parents and station_id not in self.stations:
                source = parents[station_id]
                names = self._translations.get(station_id, {})
                self.stations[station_id] = Station(
                    code=station_id,
                    name_fr=clean_station_name(source["stop_name"]),
                    name_ar=names.get("ar"),
                    name_en=names.get("en"),
                    latitude=float(source["stop_lat"]) if source.get("stop_lat") else None,
                    longitude=float(source["stop_lon"]) if source.get("stop_lon") else None,
                )

    def _to_trip(self, row: dict) -> TripInfo:
        return TripInfo(
            trip_id=row["trip_id"],
            route_id=row.get("route_id", ""),
            service_id=row.get("service_id", ""),
            train_number=(row.get("trip_short_name") or "").strip(),
            headsign=clean_route_name(row.get("trip_headsign") or ""),
            direction_id=row.get("direction_id"),
            shape_id=(row.get("shape_id") or "").strip() or None,
        )

    # --- geometry -------------------------------------------------------
    def _load_shapes(self) -> dict[str, list[tuple[float, float]]]:
        try:
            rows = self._read("shapes.txt")
        except FileNotFoundError:
            return {}
        grouped: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for row in rows:
            try:
                grouped[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
                )
            except (KeyError, ValueError):
                continue
        return {
            shape_id: [(lat, lon) for _, lat, lon in sorted(points)]
            for shape_id, points in grouped.items()
        }

    def shape(self, shape_id: str | None) -> list[tuple[float, float]]:
        if not shape_id:
            return []
        if not hasattr(self, "_shapes"):
            self._shapes = self._load_shapes()
        return self._shapes.get(shape_id, [])

    def trip_shape(self, trip_id: str) -> list[tuple[float, float]]:
        trip = self._trips_by_id.get(trip_id)
        return self.shape(trip.shape_id) if trip else []

    def network(self, tolerance: float = 0.008) -> list[dict]:
        """One simplified polyline per route direction, for the overview map."""
        from .geo import simplify_polyline

        routes = {row["route_id"]: row for row in self._route_rows}
        seen: set[tuple[str, str]] = set()
        result: list[dict] = []
        for trip in self._trip_rows:
            route_id = trip.get("route_id") or ""
            shape_id = (trip.get("shape_id") or "").strip()
            direction = trip.get("direction_id") or "0"
            key = (route_id, direction)
            if key in seen:
                continue
            points = self.shape(shape_id) if shape_id else []
            approximate = False
            if len(points) < 2:
                # Coach routes carry no shapes in the feed. Draw them from the
                # ordered stop coordinates, flagged as an approximation.
                rows = self._times_by_trip.get(trip.get("trip_id", "")) or []
                points = [
                    self._stop_coords[row["stop_id"]]
                    for row in rows
                    if row.get("stop_id") in self._stop_coords
                ]
                approximate = True
            if len(points) < 2:
                continue
            seen.add(key)
            route = routes.get(route_id, {})
            route_type = route.get("route_type") or ""
            product = clean_route_name(
                (route.get("route_short_name") or "").strip()
                or ("Supratours" if route_type == "202" else "Rail")
            )
            result.append(
                {
                    "route_id": route_id,
                    "product": product,
                    "name": clean_route_name(route.get("route_long_name") or product),
                    "color": f"#{route.get('route_color') or '888888'}",
                    "type": route_type,
                    "coach": route_type == "202",
                    "approximate": approximate,
                    "shape": [[round(lat, 5), round(lon, 5)] for lat, lon in simplify_polyline(points, tolerance)],
                }
            )
        return result

    def route_shape_between(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        *,
        product: str = "",
        max_km: float = 30.0,
    ) -> list[tuple[float, float]]:
        """Best GTFS route shape passing near both endpoints.

        Some official trains (notably Al Boraq services) are not in the GTFS
        feed, so the trip has no ``shape_id``. The route shapes are still there;
        pick the one that comes closest to the two stations and trim it from
        origin to destination. This is static route geometry, not a live path.
        """
        from .geo import haversine_m, nearest_point_index

        routes = [r for r in self.network(tolerance=0.004) if not r.get("coach")]
        if product:
            preferred = [r for r in routes if (r.get("product") or "") == product]
            if preferred:
                routes = preferred

        best: tuple[float, list[tuple[float, float]]] | None = None
        for route in routes:
            shape = route.get("shape") or []
            if len(shape) < 2:
                continue
            i = nearest_point_index(shape, start_lat, start_lon)
            j = nearest_point_index(shape, end_lat, end_lon)
            if i == j:
                continue
            start_m = haversine_m(shape[i][0], shape[i][1], start_lat, start_lon)
            end_m = haversine_m(shape[j][0], shape[j][1], end_lat, end_lon)
            if start_m > max_km * 1000 or end_m > max_km * 1000:
                continue
            segment = shape[min(i, j): max(i, j) + 1]
            if i > j:
                segment = list(reversed(segment))
            score = start_m + end_m
            if best is None or score < best[0]:
                best = (score, [(float(p[0]), float(p[1])) for p in segment])
        return best[1] if best else []

    # --- service calendar ----------------------------------------------
    def service_runs_on(self, service_id: str, travel_date: date) -> bool:
        for row in self._calendar_dates:
            if row["service_id"] == service_id and row["date"] == travel_date.strftime("%Y%m%d"):
                return row.get("exception_type") == "1"
        for row in self._calendar_rows:
            if row["service_id"] != service_id:
                continue
            start = datetime.strptime(row["start_date"], "%Y%m%d").date()
            end = datetime.strptime(row["end_date"], "%Y%m%d").date()
            if not (start <= travel_date <= end):
                return False
            return row[WEEKDAY_FIELDS[travel_date.weekday()]] == "1"
        return False

    # --- queries --------------------------------------------------------
    def station(self, code: str) -> Station | None:
        return self.stations.get(str(code))

    def station_code_for_stop(self, stop_id: str) -> str:
        return self._stop_to_station.get(stop_id, stop_id)

    def trips_between(
        self, origin_code: str, destination_code: str, travel_date: date, train_number: str | None = None
    ) -> list[TripInfo]:
        rows = self.trips_between_sets([origin_code], [destination_code], travel_date, train_number)
        rows.sort(
            key=lambda item: gtfs_time_to_datetime(travel_date, item[1].get("departure_time"))
            or datetime.max.replace(tzinfo=TZ)
        )
        return [trip for trip, _, _ in rows]

    @staticmethod
    def _clock_seconds(value: str | None) -> int | None:
        if not value:
            return None
        parts = value.split(":")
        if len(parts) not in (2, 3):
            return None
        try:
            numbers = [int(part) for part in parts]
        except (TypeError, ValueError):
            return None
        hours, minutes = numbers[0], numbers[1]
        seconds = numbers[2] if len(numbers) == 3 else 0
        if hours < 0 or not (0 <= minutes <= 59 and 0 <= seconds <= 59):
            return None
        return hours * 3600 + minutes * 60 + seconds

    def _trip_is_plausible(self, trip_id: str) -> bool:
        """Reject trips whose own stop times are physically impossible.

        The community feed contains a few malformed tails (a 30-hour domestic
        train, a 20-hour hop between adjacent coach stops, a 433 km/h coach
        segment). Serving those produces time-travel stop lists, so they are
        excluded rather than displayed as real schedules.
        """
        cached = self._plausible_cache.get(trip_id)
        if cached is not None:
            return cached
        rows = self._times_by_trip.get(trip_id) or []
        if len(rows) < 2:
            self._plausible_cache[trip_id] = True
            return True
        first = rows[0]
        last = rows[-1]
        start = self._clock_seconds(first.get("departure_time") or first.get("arrival_time"))
        end = self._clock_seconds(last.get("arrival_time") or last.get("departure_time"))
        if start is None or end is None:
            self._plausible_cache[trip_id] = False
            return False
        duration = end - start
        if duration <= 0 or duration > 24 * 3600:
            self._plausible_cache[trip_id] = False
            return False

        previous_departure = start
        previous_stop = first.get("stop_id")
        for row in rows[1:]:
            arrival = self._clock_seconds(row.get("arrival_time") or row.get("departure_time"))
            departure = self._clock_seconds(row.get("departure_time") or row.get("arrival_time"))
            if arrival is None or departure is None:
                self._plausible_cache[trip_id] = False
                return False
            gap = arrival - previous_departure
            if gap < 0 or gap > 16 * 3600:
                self._plausible_cache[trip_id] = False
                return False
            a = self._stop_coords.get(previous_stop or "")
            b = self._stop_coords.get(row.get("stop_id") or "")
            if a and b and gap > 0:
                speed_mps = haversine_m(a[0], a[1], b[0], b[1]) / gap
                if speed_mps > 250 / 3.6:
                    self._plausible_cache[trip_id] = False
                    return False
            previous_departure = departure
            previous_stop = row.get("stop_id")
        self._plausible_cache[trip_id] = True
        return True

    def trips_on(self, travel_date: date) -> list[TripInfo]:
        return [
            trip
            for trip in self._trips_by_id.values()
            if self.service_runs_on(trip.service_id, travel_date)
            and self._trip_is_plausible(trip.trip_id)
        ]

    def trips_for_train(self, train_number: str, travel_date: date) -> list[TripInfo]:
        return [trip for trip in self.trips_on(travel_date) if trip.train_number == train_number]

    def trips_between_sets(
        self,
        origin_codes: list[str] | set[str],
        destination_codes: list[str] | set[str],
        travel_date: date,
        train_number: str | None = None,
    ) -> list[tuple[TripInfo, dict, dict]]:
        """Trips calling in ``origin_codes`` before ``destination_codes``.

        Returns ``(trip, boarding_row, alighting_row)`` where the boarding stop
        is the first call inside the origin city and the alighting stop is the
        first call inside the destination city after it.
        """
        origins = set(origin_codes)
        destinations = set(destination_codes)
        candidates = (
            self.trips_for_train(train_number, travel_date) if train_number else self.trips_on(travel_date)
        )
        results: list[tuple[TripInfo, dict, dict]] = []
        for trip in candidates:
            rows = self._times_by_trip.get(trip.trip_id) or []
            codes = [self._stop_to_station.get(r["stop_id"], "") for r in rows]
            origin_indexes = [i for i, code in enumerate(codes) if code in origins]
            if not origin_indexes:
                continue
            pairs = [
                (i, j)
                for i in origin_indexes
                for j in range(i + 1, len(codes))
                if codes[j] in destinations
            ]
            if not pairs:
                continue
            i, j = min(pairs)
            results.append((trip, rows[i], rows[j]))
        return results

    def trip_departure(self, trip_id: str, travel_date: date) -> datetime | None:
        rows = self._times_by_trip.get(trip_id) or []
        return gtfs_time_to_datetime(travel_date, rows[0].get("departure_time")) if rows else None

    def direct_destinations(
        self,
        origin_code: str,
        travel_date: date,
        *,
        after: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Every downstream station reachable without changing trains.

        Built entirely from the GTFS stop sequences: for each trip serving
        ``origin_code``, collect every later stop and keep the earliest
        departure per destination. No upstream API call is involved.
        """
        origin = str(origin_code)
        routes = {row["route_id"]: row for row in self._route_rows}
        best: dict[str, dict] = {}

        for trip in self.trips_on(travel_date):
            rows = self._times_by_trip.get(trip.trip_id) or []
            codes = [self._stop_to_station.get(row["stop_id"], row["stop_id"]) for row in rows]
            try:
                origin_index = codes.index(origin)
            except ValueError:
                continue

            route = routes.get(trip.route_id, {})
            route_type = str(route.get("route_type") or "")
            product, product_color = self.route_info(trip.route_id)
            for row in rows[origin_index + 1 :]:
                destination_code = self._stop_to_station.get(row["stop_id"], row["stop_id"])
                if destination_code == origin:
                    continue
                departure = gtfs_time_to_datetime(travel_date, rows[origin_index].get("departure_time"))
                arrival = gtfs_time_to_datetime(travel_date, row.get("arrival_time"))
                if departure is None:
                    continue
                if after is not None and departure <= after:
                    continue
                destination = self.stations.get(destination_code) or Station(
                    code=destination_code, name_fr=destination_code
                )
                duration = None
                if arrival is not None and arrival >= departure:
                    duration = int((arrival - departure).total_seconds() // 60)
                item = {
                    "code": destination_code,
                    "name": destination.name_fr,
                    "ar": destination.name_ar,
                    "departure": departure.isoformat(),
                    "departure_ms": int(departure.timestamp() * 1000),
                    "arrival": arrival.isoformat() if arrival else None,
                    "duration_minutes": duration,
                    "train": trip.train_number,
                    "trip_id": trip.trip_id,
                    "route_id": trip.route_id,
                    "product": product,
                    "product_color": product_color,
                    "coach": route_type == "202",
                }
                current = best.get(destination_code)
                if current is None or item["departure"] < current["departure"]:
                    best[destination_code] = item

        return sorted(best.values(), key=lambda item: item["departure"])[:limit]

    def route_info(self, route_id: str) -> tuple[str, str]:
        """``(product_name, colour)`` for a route, e.g. ("Al Boraq", "#E82030")."""
        row = next((r for r in self._route_rows if r["route_id"] == route_id), None)
        if row is None:
            return "", ""
        name = (row.get("route_short_name") or "").strip()
        if not name and row.get("route_type") == "202":
            name = "Supratours"
        color = row.get("route_color") or ""
        return clean_route_name(name), (f"#{color}" if color else "")

    def journey(self, trip_id: str, travel_date: date) -> Journey:
        if not self._trip_is_plausible(trip_id):
            raise LookupError(f"GTFS trip {trip_id} has implausible stop times")
        trip = self._trips_by_id[trip_id]
        product, product_color = self.route_info(trip.route_id)
        stops: list[Stop] = []
        for row in self._times_by_trip[trip_id]:
            station_id = self._stop_to_station.get(row["stop_id"], row["stop_id"])
            station = self.stations.get(station_id) or Station(code=station_id, name_fr=station_id)
            stops.append(
                Stop(
                    station=station,
                    sequence=int(row["stop_sequence"]),
                    arrival=gtfs_time_to_datetime(travel_date, row.get("arrival_time")),
                    departure=gtfs_time_to_datetime(travel_date, row.get("departure_time")),
                    status=DataType.SCHEDULED_UNOFFICIAL,
                )
            )
        journey = Journey(
            service_id=trip.trip_id,
            service_label=trip.train_number or trip.trip_id,
            travel_date=travel_date,
            stops=stops,
            provenance=self.provenance(),
            notes=[
                "Intermediate-station arrival/departure split is inferred by the "
                "feed author from ONCF's one-time-per-station grilles horaires.",
            ],
            product=product,
            product_color=product_color,
        )
        for stop in journey.stops:
            stop.total_stops = len(journey.stops)
        return journey

    def journey_for_train(
        self, train_number: str, travel_date: date, *, origin_code: str, destination_code: str
    ) -> Journey | None:
        matches = self.trips_between(origin_code, destination_code, travel_date, train_number=train_number)
        if not matches:
            return None
        return self.journey(matches[0].trip_id, travel_date)

    def provenance(self) -> Provenance:
        version = self._feed_info.get("feed_version")
        try:
            retrieved = datetime.fromtimestamp((self.directory / "stop_times.txt").stat().st_mtime, tz=TZ)
        except OSError:
            retrieved = None
        return Provenance(
            name=f"oncf-gtfs-unofficial (feed {version or 'unknown'})",
            url="https://github.com/orhazal/oncf-gtfs-unofficial",
            retrieved_at=retrieved,
            data_type=DataType.SCHEDULED_UNOFFICIAL,
        )
