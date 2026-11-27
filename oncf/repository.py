"""Repository: coordinates the official gateway and the GTFS feed.

Rules it enforces:
  * a journey's *shape* (ordered stations) may come from GTFS;
  * a journey's *times* are authoritative only when they come from ONCF itself;
  * hydration never guesses: if a train is absent from the official answer, the
    value stays ``None`` and the reason is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from .api import Leg, OfficialOncfClient
from .config import CACHE_DIR, ONCF_BASE_URL
from .gtfs import GtfsFeed
from .models import DataType, Journey, Provenance, Stop, StopKind
from .products import from_code, from_name
from .stations import StationCatalog
from .timeutil import TZ

#: Longest dwell we will accept between an arrival and its matching departure.
#: 12 h comfortably covers long border/crew stops while rejecting the 24 h
#: error you get when the previous night's service is matched by mistake.
MAX_STOP_WINDOW = timedelta(hours=12)


def trim_to_segment(journey: Journey, origin_codes: list[str], destination_codes: list[str]) -> Journey:
    """Keep only the stops between the requested boarding and alighting points.

    A Casa -> Rabat passenger should not see Marrakech or Fès just because the
    train runs further. Sequence numbers are renumbered from 1 and ``total_stops``
    is updated so terminus/origin classification stays correct.
    """
    board = next(
        (index for index, stop in enumerate(journey.stops) if stop.station.code in origin_codes),
        0,
    )
    alight = next(
        (
            index
            for index, stop in enumerate(journey.stops)
            if index > board and stop.station.code in destination_codes
        ),
        len(journey.stops) - 1,
    )
    segment = journey.stops[board : alight + 1]
    total = len(segment)
    stops = [
        replace(stop, sequence=index + 1, total_stops=total)
        for index, stop in enumerate(segment)
    ]
    trimmed = replace(journey, stops=stops)
    if board > 0 or alight < len(journey.stops) - 1:
        trimmed.notes = [
            *journey.notes,
            f"Showing {stops[0].station.display_name} → {stops[-1].station.display_name}; "
            f"train {journey.service_label} continues beyond this segment.",
        ]
    return trimmed


@dataclass(frozen=True)
class ServiceOption:
    train_number: str
    departure: datetime | None
    arrival: datetime | None
    is_direct: bool
    data_type: DataType
    origin_code: str
    destination_code: str
    origin_name: str = ""
    destination_name: str = ""
    trip_id: str | None = None
    product: str = ""
    product_color: str = ""

    @property
    def duration_minutes(self) -> int | None:
        if not self.departure or not self.arrival:
            return None
        return int((self.arrival - self.departure).total_seconds() // 60)


class Repository:
    def __init__(
        self,
        catalog: StationCatalog,
        official: OfficialOncfClient | None = None,
        gtfs: GtfsFeed | None = None,
        grids=None,
    ):
        self.catalog = catalog
        self.official = official
        self.gtfs = gtfs
        self.grids = grids

    # --- search ---------------------------------------------------------
    def search(
        self, origin_code: str, destination_code: str, travel_date: date, prefer: str = "auto"
    ) -> list[ServiceOption]:
        """List services, preferring ONCF's own answer over the GTFS mirror."""
        if prefer != "gtfs" and self.official is not None:
            try:
                legs = self.official.schedule(origin_code, destination_code, travel_date)
                options = []
                for leg in legs:
                    if not leg.is_direct:
                        continue
                    product, product_color = from_code(leg.product_code)
                    options.append(
                        ServiceOption(
                            train_number="+".join(leg.train_numbers) or "?",
                            departure=leg.departure,
                            arrival=leg.arrival,
                            is_direct=leg.is_direct,
                            data_type=DataType.SCHEDULED_OFFICIAL,
                            origin_code=leg.origin_code,
                            destination_code=leg.destination_code,
                            origin_name=self._station_name(leg.origin_code),
                            destination_name=self._station_name(leg.destination_code),
                            product=product,
                            product_color=product_color,
                        )
                    )
                if options or prefer == "official":
                    return options
            except Exception:
                if self.gtfs is None or prefer == "official":
                    raise

        if self.gtfs is None:
            return []

        options = []
        for trip in self.gtfs.trips_between(origin_code, destination_code, travel_date):
            journey = self.gtfs.journey(trip.trip_id, travel_date)
            codes = [s.station.code for s in journey.stops]
            if origin_code not in codes or destination_code not in codes:
                continue
            start = journey.stops[codes.index(origin_code)]
            end = journey.stops[codes.index(destination_code)]
            product, product_color = self.gtfs.route_info(trip.route_id)
            options.append(
                ServiceOption(
                    train_number=trip.train_number,
                    departure=start.departure or start.arrival,
                    arrival=end.arrival or end.departure,
                    is_direct=True,
                    data_type=DataType.SCHEDULED_UNOFFICIAL,
                    origin_code=origin_code,
                    destination_code=destination_code,
                    product=product,
                    product_color=product_color,
                )
            )
        return options

    # --- city-aware search ---------------------------------------------
    def _station_name(self, code: str) -> str:
        station = self.catalog.maybe_get(code)
        return station.display_name if station else code

    def search_between_sets(
        self,
        origin_codes: list[str],
        destination_codes: list[str],
        travel_date: date,
        prefer: str = "auto",
    ) -> list[ServiceOption]:
        """All services between two *groups* of stations (i.e. two cities).

        Uses the GTFS feed to enumerate trains and to learn which station each
        train actually calls at, then upgrades the times of each distinct
        station pair from ONCF. This avoids querying every pair blindly: a
        Casablanca→Rabat search has 21 station combinations but only a handful
        of pairs that any train actually serves.
        """
        if self.gtfs is not None:
            candidates = self.gtfs.trips_between_sets(origin_codes, destination_codes, travel_date)
            if candidates:
                return self._options_from_gtfs(candidates, travel_date, prefer)

        if prefer == "gtfs" or self.official is None:
            return []
        return self._options_from_official_pairs(origin_codes, destination_codes, travel_date)

    def _options_from_gtfs(
        self, candidates, travel_date: date, prefer: str
    ) -> list[ServiceOption]:
        from .timeutil import gtfs_time_to_datetime

        assert self.gtfs is not None
        records = []
        for trip, board, alight in candidates:
            origin = self.gtfs.station_code_for_stop(board["stop_id"])
            destination = self.gtfs.station_code_for_stop(alight["stop_id"])
            records.append(
                {
                    "train": trip.train_number,
                    "origin": origin,
                    "destination": destination,
                    "departure": gtfs_time_to_datetime(travel_date, board.get("departure_time")),
                    "arrival": gtfs_time_to_datetime(travel_date, alight.get("arrival_time")),
                    "trip_id": trip.trip_id,
                    "route_id": trip.route_id,
                }
            )

        pairs = sorted({(record["origin"], record["destination"]) for record in records})
        options: dict[str, ServiceOption] = {}
        official_used = False

        # ONCF first: its answer defines which services are actually sold. The
        # gateway returns every train on a pair, including ones the GTFS feed
        # missed, so starting from GTFS would silently drop them.
        if prefer != "gtfs" and self.official is not None:
            for origin, destination in pairs:
                try:
                    legs = self.official.schedule(origin, destination, travel_date)
                except Exception:
                    continue
                for leg in legs:
                    if not leg.is_direct:
                        continue
                    label = "+".join(leg.train_numbers)
                    if not label:
                        continue
                    official_used = True
                    product, product_color = from_code(leg.product_code) if leg.is_direct else ("", "")
                    option = ServiceOption(
                        train_number=label,
                        departure=leg.departure,
                        arrival=leg.arrival,
                        is_direct=leg.is_direct,
                        data_type=DataType.SCHEDULED_OFFICIAL,
                        origin_code=origin,
                        destination_code=destination,
                        origin_name=self._station_name(origin),
                        destination_name=self._station_name(destination),
                        product=product,
                        product_color=product_color,
                    )
                    current = options.get(label)
                    if current is None or (
                        option.departure is not None
                        and (current.departure is None or option.departure < current.departure)
                    ):
                        options[label] = option

        for record in records:
            if record["train"] in options:
                continue

            # A GTFS-only train is only trusted when an official grid prints it.
            # Otherwise it is either a feed artefact or a service ONCF does not
            # sell for this pair, and showing it would be misleading.
            grid_times = self._grid_pair_times(
                record["train"], record["origin"], record["destination"], travel_date
            )
            if grid_times is not None:
                departure, arrival, grid = grid_times
                product, product_color = self.gtfs.route_info(record["route_id"])
                options[record["train"]] = ServiceOption(
                    train_number=record["train"],
                    departure=departure,
                    arrival=arrival,
                    is_direct=True,
                    data_type=DataType.SCHEDULED_OFFICIAL,
                    origin_code=record["origin"],
                    destination_code=record["destination"],
                    origin_name=self._station_name(record["origin"]),
                    destination_name=self._station_name(record["destination"]),
                    trip_id=record["trip_id"],
                    product=product,
                    product_color=product_color,
                )
            elif not official_used:
                product, product_color = self.gtfs.route_info(record["route_id"])
                options[record["train"]] = ServiceOption(
                    train_number=record["train"],
                    departure=record["departure"],
                    arrival=record["arrival"],
                    is_direct=True,
                    data_type=DataType.SCHEDULED_UNOFFICIAL,
                    origin_code=record["origin"],
                    destination_code=record["destination"],
                    origin_name=self._station_name(record["origin"]),
                    destination_name=self._station_name(record["destination"]),
                    trip_id=record["trip_id"],
                    product=product,
                    product_color=product_color,
                )

        return sorted(
            options.values(),
            key=lambda option: option.departure or datetime.max.replace(tzinfo=TZ),
        )

    def _grid_pair_times(
        self, train: str, origin_code: str, destination_code: str, travel_date: date
    ):
        """Printed departure/arrival at two stations, from the official grids."""
        if self.grids is None:
            return None
        from .grid import find_trip, parse_clock, station_matches

        origin_name = self._station_name(origin_code)
        destination_name = self._station_name(destination_code)
        for grid in self.grids.grids:
            trip = find_trip(grid, train)
            if trip is None:
                continue
            printed_departure = next(
                (time for label, time in trip.times.items() if station_matches(label, origin_name)),
                None,
            )
            printed_arrival = next(
                (time for label, time in trip.times.items() if station_matches(label, destination_name)),
                None,
            )
            if not printed_departure or not printed_arrival:
                continue
            departure_minutes = parse_clock(printed_departure)
            arrival_minutes = parse_clock(printed_arrival)
            if departure_minutes is None or arrival_minutes is None:
                continue
            base = datetime.combine(travel_date, datetime.min.time(), tzinfo=TZ)
            departure = base + timedelta(minutes=departure_minutes)
            arrival = base + timedelta(minutes=arrival_minutes)
            if arrival < departure:
                arrival += timedelta(days=1)
            return departure, arrival, grid
        return None

    def _options_from_official_pairs(
        self, origin_codes: list[str], destination_codes: list[str], travel_date: date
    ) -> list[ServiceOption]:
        assert self.official is not None
        options: dict[tuple[str, str], ServiceOption] = {}
        queries = 0
        for origin in origin_codes:
            for destination in destination_codes:
                if queries >= 24:
                    break
                queries += 1
                try:
                    legs = self.official.schedule(origin, destination, travel_date)
                except Exception:
                    continue
                for leg in legs:
                    if not leg.is_direct:
                        continue
                    option = ServiceOption(
                        train_number="+".join(leg.train_numbers) or "?",
                        departure=leg.departure,
                        arrival=leg.arrival,
                        is_direct=leg.is_direct,
                        data_type=DataType.SCHEDULED_OFFICIAL,
                        origin_code=origin,
                        destination_code=destination,
                        origin_name=self._station_name(origin),
                        destination_name=self._station_name(destination),
                    )
                    key = (option.train_number, option.origin_code, option.destination_code)
                    options.setdefault(key, option)
        return sorted(
            options.values(),
            key=lambda option: option.departure or datetime.max.replace(tzinfo=TZ),
        )

    def journey_between_sets(
        self,
        train_number: str,
        origin_codes: list[str],
        destination_codes: list[str],
        travel_date: date,
    ) -> Journey:
        """Full stop list for a train, given city-level endpoints."""
        if self.gtfs is not None:
            candidates = self.gtfs.trips_between_sets(
                origin_codes, destination_codes, travel_date, train_number=train_number
            )
            if candidates:
                return self.gtfs.journey(candidates[0][0].trip_id, travel_date)

        grid_journey = self.journey_from_grid(train_number, travel_date, origin_codes, destination_codes)
        if grid_journey is not None:
            return grid_journey

        if self.official is not None:
            for origin in origin_codes:
                for destination in destination_codes:
                    leg = self._find_leg(train_number, origin, destination, travel_date)
                    if leg is not None:
                        return self.official_journey(train_number, origin, destination, travel_date)

        raise LookupError(
            f"train {train_number} does not run between these stations on {travel_date}"
        )

    # --- official grid PDFs ---------------------------------------------
    def _catalog_station_for(self, label: str):
        """Resolve a grid label without merging genuinely different stations.

        ``station_matches`` is intentionally loose for *searching*: it treats
        "Salé Tabriquet" as matching "Salé". For identity that is wrong — the
        grid has both "Salé Ville" and "Salé Tabriquet", and both were becoming
        one SALE stop. Here we require an exact normalized name, or the common
        "X Ville" ↔ "X" alias, and otherwise keep a distinct synthetic station.
        """
        from .grid import normalize_name
        from .models import Station

        target = normalize_name(label)
        target_tokens = target.split()
        stations = list(self.catalog.all())

        for station in stations:
            if normalize_name(station.name_fr) == target:
                return station

        if target_tokens and target_tokens[-1] == "ville":
            base = " ".join(target_tokens[:-1])
            for station in stations:
                if normalize_name(station.name_fr) == base:
                    return station

        for station in stations:
            name_tokens = normalize_name(station.name_fr).split()
            if name_tokens and name_tokens[-1] == "ville" and " ".join(name_tokens[:-1]) == target:
                return station

        # A few grid spellings differ by one letter (Mohammadia/Mohammedia).
        # Accept a near-identical unique name, but never the loose substring
        # match that merged Salé Ville with Salé Tabriquet.
        from difflib import SequenceMatcher

        scored = sorted(
            (
                (SequenceMatcher(None, normalize_name(station.name_fr), target).ratio(), station)
                for station in stations
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if scored and scored[0][0] >= 0.9:
            if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05:
                return scored[0][1]

        return Station(code=target, name_fr=label)

    def journey_from_grid(
        self,
        train_number: str,
        travel_date: date,
        origin_codes: list[str] | None = None,
        destination_codes: list[str] | None = None,
    ) -> Journey | None:
        """Build a stop list from an official grid PDF.

        Grids print one time per station (the departure), so intermediate stops
        carry a departure only and no dwell. That is honest: the PDF cannot prove
        a stop duration.
        """
        if self.grids is None:
            return None

        from .grid import find_trip, parse_clock
        from .timeutil import TZ

        for grid in self.grids.grids:
            trip = find_trip(grid, train_number)
            if trip is None:
                continue

            items = list(trip.times.items())
            stops: list[Stop] = []
            day_offset = 0
            previous = None
            for index, (label, clock) in enumerate(items):
                minutes = parse_clock(clock)
                if minutes is None:
                    continue
                if previous is not None and minutes < previous:
                    day_offset += 1
                previous = minutes
                moment = datetime.combine(travel_date, datetime.min.time(), tzinfo=TZ) + timedelta(
                    days=day_offset, minutes=minutes
                )
                station = self._catalog_station_for(label)
                is_last = index == len(items) - 1
                stops.append(
                    Stop(
                        station=station,
                        sequence=index + 1,
                        arrival=moment if is_last else None,
                        departure=None if is_last else moment,
                        status=DataType.SCHEDULED_OFFICIAL,
                    )
                )

            for stop in stops:
                stop.total_stops = len(stops)

            if not stops:
                continue
            codes = [stop.station.code for stop in stops]
            if origin_codes and not any(code in origin_codes for code in codes):
                continue
            if destination_codes and not any(code in destination_codes for code in codes):
                continue

            try:
                retrieved = datetime.fromtimestamp(grid.source.stat().st_mtime, tz=TZ)
            except OSError:
                retrieved = None

            return Journey(
                service_id=f"grid:{train_number}:{grid.effective}",
                service_label=train_number,
                travel_date=travel_date,
                stops=stops,
                provenance=Provenance(
                    name=f"ONCF official grid PDF ({grid.source.name})",
                    url=None,
                    retrieved_at=retrieved,
                    data_type=DataType.SCHEDULED_OFFICIAL,
                ),
                notes=[
                    f"Stop list and times from {grid.source.name}, effective "
                    f"{grid.effective}. The grid prints one time per station "
                    "(departure), so dwell times are unavailable until refreshed "
                    "from ONCF."
                ],
            )
        return None

    # --- journey shape --------------------------------------------------
    def journey(
        self,
        train_number: str,
        origin_code: str,
        destination_code: str,
        travel_date: date,
        *,
        source: str = "auto",
    ) -> Journey:
        """Return the stop list for one train.

        ``source``: ``gtfs`` (ordered stops, unofficial times), ``official``
        (origin/destination only, official times) or ``auto`` (GTFS when the
        feed knows the train, otherwise official).
        """
        if source in {"auto", "gtfs"} and self.gtfs is not None:
            journey = self.gtfs.journey_for_train(
                train_number, travel_date, origin_code=origin_code, destination_code=destination_code
            )
            if journey is not None:
                return journey
            if source == "gtfs":
                raise LookupError(f"train {train_number} not found in the GTFS feed for {travel_date}")

        if self.official is None:
            raise LookupError("train not found in GTFS and no official client configured")

        return self.official_journey(train_number, origin_code, destination_code, travel_date)

    def official_journey(
        self, train_number: str, origin_code: str, destination_code: str, travel_date: date
    ) -> Journey:
        assert self.official is not None
        leg = self.official.direct_leg(origin_code, destination_code, travel_date, train_number)
        if leg is None:
            raise LookupError(
                f"train {train_number} does not run directly {origin_code}->{destination_code} on {travel_date}"
            )
        stops = [
            Stop(
                station=self.catalog.get(origin_code),
                sequence=1,
                departure=leg.departure,
                status=DataType.SCHEDULED_OFFICIAL,
                total_stops=2,
            ),
            Stop(
                station=self.catalog.get(destination_code),
                sequence=2,
                arrival=leg.arrival,
                status=DataType.SCHEDULED_OFFICIAL,
                total_stops=2,
            ),
        ]
        product, product_color = from_code(leg.product_code)
        return Journey(
            service_id=f"official:{train_number}:{travel_date.isoformat()}",
            service_label=train_number,
            travel_date=travel_date,
            stops=stops,
            provenance=Provenance(
                name="API gateway /api/schedule",
                url=f"{ONCF_BASE_URL}/info-travaux",
                retrieved_at=self.official.last_retrieved_at,
                data_type=DataType.SCHEDULED_OFFICIAL,
            ),
            notes=["Official answer for origin/destination only; intermediate stops not requested."],
            product=product,
            product_color=product_color,
        )

    # --- authoritative stop hydration -----------------------------------
    def official_stop_times(
        self,
        train_number: str,
        origin_code: str,
        destination_code: str,
        station_code: str,
        travel_date: date,
    ) -> tuple[datetime | None, datetime | None, list[str]]:
        """Ask ONCF for the arrival into and departure from ``station_code``.

        Two sub-legs are queried: origin->station gives the arrival, station->
        destination gives the departure. Nothing is derived from the GTFS.

        A mid-journey station is tricky: the departure from it may fall on the
        next calendar day, and ONCF answers "what leaves this station on date D".
        Querying the travel date alone can therefore return the *previous*
        night's train (01:40 on D instead of 01:40 on D+1). The departure is only
        accepted when it falls after the arrival and within a plausible dwell
        window, otherwise neighbouring service dates are tried.
        """
        if self.official is None:
            raise RuntimeError("no official client configured")

        notes: list[str] = []
        arrival = departure = None

        if station_code == origin_code:
            leg = self._find_leg(train_number, origin_code, destination_code, travel_date)
            if leg is None:
                notes.append("train not listed by ONCF for this origin/destination/date")
            else:
                departure = leg.departure
            return arrival, departure, notes

        if station_code == destination_code:
            leg = self._find_leg(train_number, origin_code, destination_code, travel_date)
            if leg is None:
                notes.append("train not listed by ONCF for this origin/destination/date")
            else:
                arrival = leg.arrival
            return arrival, departure, notes

        inbound = self._find_leg(train_number, origin_code, station_code, travel_date)
        if inbound is None:
            notes.append(f"ONCF lists no {train_number} leg {origin_code}->{station_code}")
        else:
            arrival = inbound.arrival

        departure = self._resolve_departure(
            train_number, station_code, destination_code, travel_date, arrival, notes
        )
        return arrival, departure, notes

    def _resolve_departure(
        self,
        train_number: str,
        station_code: str,
        destination_code: str,
        travel_date: date,
        arrival: datetime | None,
        notes: list[str],
    ) -> datetime | None:
        best: tuple[timedelta, datetime] | None = None
        fallback: datetime | None = None

        for offset in (-1, 0, 1, 2):
            candidate_date = travel_date + timedelta(days=offset)
            leg = self._find_leg(train_number, station_code, destination_code, candidate_date)
            if leg is None or leg.departure is None:
                continue
            if arrival is None:
                fallback = leg.departure if fallback is None else min(fallback, leg.departure)
                continue
            wait = leg.departure - arrival
            if timedelta(0) <= wait <= MAX_STOP_WINDOW:
                if best is None or wait < best[0]:
                    best = (wait, leg.departure)

        if best is not None:
            return best[1]
        if arrival is not None:
            notes.append(
                f"no {train_number} departure from {station_code} found within "
                f"{int(MAX_STOP_WINDOW.total_seconds() // 3600)} h after arrival; departure marked unavailable"
            )
            return None
        if fallback is not None:
            notes.append("departure time not cross-checked against an arrival time")
        else:
            notes.append(f"ONCF lists no {train_number} leg {station_code}->{destination_code}")
        return fallback

    def _find_leg(
        self, train_number: str, origin_code: str, destination_code: str, travel_date: date
    ) -> Leg | None:
        assert self.official is not None
        try:
            return self.official.direct_leg(origin_code, destination_code, travel_date, train_number)
        except Exception:
            return None

    def hydrate_stop(
        self, journey: Journey, station_code: str, *, origin_code: str, destination_code: str
    ) -> Journey:
        """Return a copy of ``journey`` with one stop replaced by official times."""
        stop = journey.stop_for(station_code)
        if stop is None:
            raise KeyError(f"station {station_code} is not on this journey")

        arrival, departure, notes = self.official_stop_times(
            journey.service_label, origin_code, destination_code, station_code, journey.travel_date
        )

        updated = replace(stop, arrival=arrival, departure=departure, status=DataType.SCHEDULED_OFFICIAL)
        stops = [updated if s is stop else s for s in journey.stops]
        provenance = Provenance(
            name="GTFS feed (stop sequence) + API gateway (times)",
            url=f"{ONCF_BASE_URL}/info-travaux",
            retrieved_at=self.official.last_retrieved_at if self.official else None,
            data_type=DataType.MIXED,
        )
        return Journey(
            service_id=journey.service_id,
            service_label=journey.service_label,
            travel_date=journey.travel_date,
            stops=stops,
            provenance=provenance,
            timezone=journey.timezone,
            notes=[*journey.notes, *notes],
        )

    def _pair_leg(
        self,
        train_number: str,
        first: Stop,
        second: Stop,
        travel_date: date,
    ) -> Leg | None:
        """Official direct leg for one consecutive station pair.

        The requested date is the service date, but a pair crossing midnight may
        be listed by ONCF on the next calendar date. Candidate dates are scored
        against the known grid departure (or arrival) instead of guessed.
        """
        if self.official is None or first.station.code == second.station.code:
            return None
        reference = first.departure or second.arrival
        best: tuple[float, Leg] | None = None
        for offset in (0, 1, -1, 2):
            candidate_date = travel_date + timedelta(days=offset)
            try:
                leg = self.official.direct_leg(
                    first.station.code,
                    second.station.code,
                    candidate_date,
                    train_number,
                )
            except Exception:
                leg = None
            if leg is None or leg.departure is None or leg.arrival is None:
                continue
            score = (
                abs((leg.departure - reference).total_seconds())
                if reference is not None
                else 0.0
            )
            if score > 18 * 3600:
                continue
            if best is None or score < best[0]:
                best = (score, leg)
            if offset == 0 and score <= 2 * 3600:
                return leg
        return best[1] if best else None

    def hydrate_all_stops(self, journey: Journey) -> Journey:
        """Verify every consecutive stop pair against ONCF's official schedule.

        Each pair yields the departure from the first station and the arrival at
        the second, so an intermediate stop gets an exact arrival from the
        previous leg and an exact departure from the next one. Missing pairs are
        reported, never filled with guessed times.
        """
        if self.official is None:
            raise RuntimeError("no official client configured")
        stops = list(journey.stops)
        if len(stops) < 2:
            return journey

        legs: list[Leg | None] = [
            self._pair_leg(journey.service_label, first, second, journey.travel_date)
            for first, second in zip(stops, stops[1:])
        ]
        updated: list[Stop] = []
        for index, stop in enumerate(stops):
            arrival = stop.arrival
            departure = stop.departure
            got_arrival = False
            got_departure = False
            if index > 0 and legs[index - 1] is not None and legs[index - 1].arrival is not None:
                arrival = legs[index - 1].arrival
                got_arrival = True
            if index < len(legs) and legs[index] is not None and legs[index].departure is not None:
                departure = legs[index].departure
                got_departure = True

            if stop.kind is StopKind.ORIGIN:
                complete = got_departure
            elif stop.kind is StopKind.DESTINATION:
                complete = got_arrival
            else:
                complete = got_arrival and got_departure

            if complete:
                status = DataType.SCHEDULED_OFFICIAL
            elif got_arrival or got_departure:
                status = DataType.MIXED
            else:
                status = stop.status
            updated.append(replace(stop, arrival=arrival, departure=departure, status=status))

        complete_stops = sum(
            1
            for stop in updated
            if (
                stop.kind is StopKind.ORIGIN and stop.shown_departure is not None
                or stop.kind is StopKind.DESTINATION and stop.shown_arrival is not None
                or stop.kind not in (StopKind.ORIGIN, StopKind.DESTINATION)
                and stop.shown_arrival is not None
                and stop.shown_departure is not None
            )
        )
        verified_pairs = sum(1 for leg in legs if leg is not None)
        missing = [
            f"{stops[i].station.name_fr} → {stops[i + 1].station.name_fr}"
            for i, leg in enumerate(legs)
            if leg is None
        ]
        notes = [
            f"ONCF official verification: {verified_pairs}/{len(legs)} consecutive legs matched; "
            f"{complete_stops}/{len(stops)} stops have a complete arrival/departure pair."
        ]
        if missing:
            shown = "; ".join(missing[:8])
            notes.append(
                f"Not verified from ONCF: {shown}" + (" …" if len(missing) > 8 else "")
            )
        provenance = Provenance(
            name="API gateway /api/schedule (all stop times)",
            url=f"{ONCF_BASE_URL}/info-travaux",
            retrieved_at=self.official.last_retrieved_at,
            data_type=(
                DataType.SCHEDULED_OFFICIAL
                if verified_pairs == len(legs)
                else DataType.MIXED
            ),
        )
        return replace(
            journey,
            stops=updated,
            provenance=provenance,
            notes=[*journey.notes, *notes],
        )


