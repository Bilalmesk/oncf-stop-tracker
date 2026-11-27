"""Local live map server.

Serves a single-page map that combines four things:

* the **route geometry** from the GTFS shapes (static);
* a **scheduled estimate** of where the train should be right now, interpolated
  from the timetable (clearly labelled, because ONCF broadcasts no positions);
* a **live station departure board** from ONCF's current mobile API (real
  departure times and delay levels, not a position);
* the **passenger's own live GPS**, supplied by the browser with consent.

Run with::

    python -m oncf live

The server binds to 127.0.0.1 only; external requests are limited to the
official ONCF endpoints used for the station catalogue, timetables, live
station departures and the works-notice CMS.
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from dataclasses import replace
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cities import CityCatalog
from .config import (
    AUTO_GRID_DIR,
    DATA_DIR,
    GRID_DIR,
    GRID_REFRESH_HOURS,
    GTFS_DIR,
    ONCF_BASE_URL,
    ONCF_MOBILE_BASE,
    ensure_dirs,
)
from .gtfs import GtfsFeed
from .grid import GridLibrary
from .grid_refresh import read_status
from .mobile import LiveDeparturesService, MobileOncfClient
from .models import Journey
from .repository import Repository, trim_to_segment
from .stations import StationCatalog
from .sources import SourceStatusService
from .timeutil import TZ, format_time
from .tracker import ScheduledProgress
from .traffic import NOTICE_NOTE, SOURCE_NAME, SOURCE_URL, TrafficService

WEB_DIR = Path(__file__).resolve().parent / "web"


def _ms(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


def journey_payload(
    journey: Journey,
    shape: list[tuple[float, float]],
    segment: tuple | None = None,
    shape_source: str = "straight",
) -> dict:
    progress = ScheduledProgress(journey, shape)
    boarding = segment[0] if segment else None
    alighting = segment[1] if segment else None
    stops = []
    for stop in journey.stops:
        stops.append(
            {
                "code": stop.station.code,
                "name": {
                    "fr": stop.station.name_fr,
                    "ar": stop.station.name_ar,
                    "en": stop.station.name_en,
                },
                "sequence": stop.sequence,
                "kind": stop.kind.value,
                "arrival": stop.shown_arrival.isoformat() if stop.shown_arrival else None,
                "departure": stop.shown_departure.isoformat() if stop.shown_departure else None,
                # Pre-formatted in Africa/Casablanca by Python. The browser's own
                # tz database can disagree about Morocco, so never reformat these.
                "arrival_time": format_time(stop.shown_arrival),
                "departure_time": format_time(stop.shown_departure),
                "arrival_ms": _ms(stop.shown_arrival),
                "departure_ms": _ms(stop.shown_departure),
                "duration_minutes": stop.duration_minutes,
                "duration_label": stop.duration_label,
                # ONCF publishes no platform/voie assignment anywhere in its
                # public data, so this is always null rather than guessed.
                "platform": stop.platform,
                "platform_available": stop.platform is not None,
                "status": stop.status.value,
                "status_label": stop.status.label,
                "lat": stop.station.latitude,
                "lon": stop.station.longitude,
                "is_boarding": bool(boarding and stop.station.code == boarding.station.code),
                "is_alighting": bool(alighting and stop.station.code == alighting.station.code),
            }
        )

    return {
        "journey": {
            "service_id": journey.service_id,
            "service_label": journey.service_label,
            "product": journey.product,
            "product_color": journey.product_color,
            "travel_date": journey.travel_date.isoformat(),
            "timezone": journey.timezone,
            "source": {
                "name": journey.provenance.name if journey.provenance else None,
                "url": journey.provenance.url if journey.provenance else None,
                "data_type": journey.provenance.data_type.value if journey.provenance else "unavailable",
                "retrieved_at": journey.provenance.retrieved_at.isoformat()
                if journey.provenance and journey.provenance.retrieved_at
                else None,
            },
            "notes": journey.notes,
        },
        "stops": stops,
        "shape": [[lat, lon] for lat, lon in shape],
        "shape_source": shape_source,
        "segment": {
            "from": {
                "code": boarding.station.code,
                "name": boarding.station.display_name,
                "departure": boarding.shown_departure.isoformat() if boarding.shown_departure else None,
                "departure_time": format_time(boarding.shown_departure),
            }
            if boarding
            else None,
            "to": {
                "code": alighting.station.code,
                "name": alighting.station.display_name,
                "arrival": alighting.shown_arrival.isoformat() if alighting.shown_arrival else None,
                "arrival_time": format_time(alighting.shown_arrival),
            }
            if alighting
            else None,
        },
        "schedule_points": progress.schedule_points(),
        "total_distance_m": progress.total_distance,
        "platform_note": (
            "ONCF does not publish platform (voie/quai) assignments in its public "
            "timetable data, so none is shown. Check the station display on arrival."
        ),
        "utc_offset_minutes": int(datetime.now(tz=TZ).utcoffset().total_seconds() // 60),
        "generated_at": datetime.now(tz=TZ).isoformat(),
        "server_time_ms": _ms(datetime.now(tz=TZ)),
    }


class LiveApp:
    """Holds the data sources so the HTTP handler stays thin."""

    def __init__(self, offline: bool = False, min_interval: float = 1.0, gtfs_dir: Path | None = None):
        from .api import OfficialOncfClient
        from .http import HttpClient

        self.catalog = StationCatalog.load(HttpClient(ONCF_BASE_URL))
        self.cities = CityCatalog.load(HttpClient(ONCF_BASE_URL), self.catalog.index())
        self.offline = offline
        self.auto_grid_dir = AUTO_GRID_DIR
        automatic = GridLibrary.load_dir(AUTO_GRID_DIR)
        self.grid_source = "auto-current" if len(automatic) else "baseline"
        self.grids = automatic if len(automatic) else GridLibrary.load_dir(GRID_DIR)
        self.grid_status = read_status()
        self._grid_refreshing = False
        self._grid_thread: threading.Thread | None = None
        feed_dir = Path(gtfs_dir or GTFS_DIR)
        self.gtfs = GtfsFeed(feed_dir) if (feed_dir / "stops.txt").exists() else None
        official = None if offline else OfficialOncfClient(min_interval=min_interval)
        if self.gtfs is None and official is None:
            raise SystemExit("Need the GTFS feed in data/orhazal or network access.")
        self.repo = Repository(self.catalog, official=official, gtfs=self.gtfs, grids=self.grids)
        mobile_news_client = (
            None if offline else HttpClient(ONCF_MOBILE_BASE, min_interval=2.0, timeout=30.0)
        )
        self.traffic_service = (
            None
            if offline
            else TrafficService(
                HttpClient(ONCF_BASE_URL, min_interval=2.0, timeout=60.0),
                news_client=mobile_news_client,
            )
        )
        self.mobile_client = None if offline else MobileOncfClient(min_interval=min_interval, base=ONCF_MOBILE_BASE)
        self.departures_service = LiveDeparturesService(self.mobile_client, self.catalog)
        self.source_status = SourceStatusService()

    def sources(self, refresh: bool = False) -> dict:
        """Plain gateway liveness checks; read-only."""
        return self.source_status.get(refresh=refresh)

    def grids_status(self) -> dict:
        dates = [grid.effective for grid in self.grids.grids if grid.effective]
        effective = max(dates) if dates else None
        return {
            "effective": effective.isoformat() if effective else None,
            "source": self.grid_source,
            "sheets": len(self.grids),
            "checked_at": self.grid_status.get("checked_at"),
            "error": self.grid_status.get("error"),
            "promoted": self.grid_status.get("promoted"),
            "refreshing": self._grid_refreshing,
        }

    def start_grid_refresh(self, force: bool = False) -> bool:
        """Check the official timetable page in the background."""
        if self.offline:
            return False
        from .grid_refresh import refresh_due

        if not self.auto_grid_dir.exists():
            force = True
        if not force and not refresh_due(GRID_REFRESH_HOURS):
            return False
        if self._grid_refreshing:
            return False
        self._grid_refreshing = True
        self._grid_thread = threading.Thread(
            target=self._refresh_grids_worker,
            name="oncf-grid-refresh",
            daemon=True,
        )
        self._grid_thread.start()
        return True

    def _refresh_grids_worker(self) -> None:
        from .grid_refresh import refresh_timetable_pdfs
        from .http import HttpClient

        try:
            client = HttpClient(ONCF_BASE_URL, min_interval=1.0, timeout=60.0)
            dates = [grid.effective for grid in self.grids.grids if grid.effective]
            current = max(dates) if dates else None
            result = refresh_timetable_pdfs(
                client,
                self.auto_grid_dir,
                current_effective=current,
            )
            library = result.pop("library", None)
            self.grid_status = result
            if library is not None:
                self.grids = library
                self.repo.grids = library
                self.grid_source = "auto-current"
        except Exception as exc:  # noqa: BLE001 - never kill the server
            self.grid_status = {
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        finally:
            self._grid_refreshing = False

    def traffic_info(self, refresh: bool = False) -> dict:
        """Official works notices from the ONCF CMS (never live positions)."""
        if self.traffic_service is None:
            return {
                "source": SOURCE_NAME,
                "source_url": SOURCE_URL,
                "language": "en",
                "fetched_at": None,
                "stale": False,
                "notices": [],
                "error": "offline mode",
                "note": NOTICE_NOTE,
            }
        return self.traffic_service.get(refresh=refresh)

    def departures(self, station: str, refresh: bool = False) -> dict:
        """Live departure board for one station, resolved by code or name."""
        text = (station or "").strip()
        if not text:
            raise KeyError("station is required")
        if text.startswith("c:"):
            raise KeyError("live departures need a station, not a city")
        try:
            resolved = self.catalog.resolve(text)
        except KeyError:
            try:
                city = self.cities.resolve(text)
            except KeyError:
                raise
            codes = ", ".join(city.station_codes[:8])
            raise KeyError(f"{text!r} is a city; choose one of its stations: {codes}") from None
        return self.departures_service.get(resolved.code, refresh=refresh)

    def _expand(self, value: str) -> tuple[list[str], str]:
        """Resolve a city (`c:33`) or a station into station codes + label."""
        text = (value or "").strip()
        if not text:
            raise KeyError("empty station or city")
        if text.startswith("c:"):
            city = self.cities.get(text)
            if not city.station_codes:
                raise KeyError(f"city {city.name_fr} has no stations")
            return list(city.station_codes), city.name_fr
        try:
            station = self.catalog.resolve(text)
            return [station.code], station.display_name
        except KeyError:
            city = self.cities.resolve(text)
            if not city.station_codes:
                raise KeyError(f"city {city.name_fr} has no stations")
            return list(city.station_codes), city.name_fr

    def network(self) -> dict:
        """Full overview: every route shape, station and city with a station."""
        routes = self.gtfs.network() if self.gtfs else []

        stations = []
        for station in self.catalog.all():
            if station.latitude is None or station.longitude is None:
                continue
            city = self.cities.maybe_get(station.city_code) if station.city_code else None
            stations.append(
                {
                    "code": station.code,
                    "name": station.name_fr,
                    "ar": station.name_ar,
                    "city": city.name_fr if city else None,
                    "lat": station.latitude,
                    "lon": station.longitude,
                }
            )

        cities = []
        for city in self.cities.search("", limit=500):
            points = [
                self.catalog.maybe_get(code)
                for code in city.station_codes
            ]
            located = [p for p in points if p and p.latitude is not None and p.longitude is not None]
            if not located:
                continue
            cities.append(
                {
                    "name": city.name_fr,
                    "ar": city.name_ar,
                    "lat": sum(p.latitude for p in located) / len(located),
                    "lon": sum(p.longitude for p in located) / len(located),
                    "stations": len(located),
                }
            )
        cities.sort(key=lambda c: (-c["stations"], c["name"]))

        return {"routes": routes, "stations": stations, "cities": cities}

    def journey(
        self,
        train: str,
        origin: str,
        destination: str,
        travel_date: date,
        official_station: str | None,
        verify_all: bool = False,
    ) -> dict:
        origin_codes, origin_label = self._expand(origin)
        destination_codes, destination_label = self._expand(destination)
        full = self.repo.journey_between_sets(train, origin_codes, destination_codes, travel_date)

        # Show only the requested segment. A Casa -> Rabat passenger does not
        # need Marrakech or Fès in the list, even if the train continues there.
        trimmed = trim_to_segment(full, origin_codes, destination_codes)

        if official_station:
            try:
                target_codes, _ = self._expand(official_station)
                target = next((s for s in trimmed.stops if s.station.code in target_codes), None)
                if target is not None:
                    trimmed = self.repo.hydrate_stop(
                        trimmed,
                        target.station.code,
                        origin_code=trimmed.stops[0].station.code,
                        destination_code=trimmed.stops[-1].station.code,
                    )
                else:
                    trimmed.notes.append(
                        f"station {official_station} is not on the requested segment"
                    )
            except Exception as exc:  # hydration is best-effort, never fatal
                trimmed.notes.append(f"official refresh failed: {exc}")

        if verify_all:
            try:
                trimmed = self.repo.hydrate_all_stops(trimmed)
            except Exception as exc:  # verification is best-effort, never fatal
                trimmed.notes.append(f"official verification failed: {exc}")

        shape = self.gtfs.trip_shape(trimmed.service_id) if self.gtfs else []
        shape_source = "trip" if len(shape) > 1 else "straight"
        if self.gtfs and len(shape) < 3 and len(trimmed.stops) >= 2:
            start = trimmed.stops[0].station
            end = trimmed.stops[-1].station
            if (
                start.latitude is not None
                and start.longitude is not None
                and end.latitude is not None
                and end.longitude is not None
            ):
                fallback = self.gtfs.route_shape_between(
                    start.latitude,
                    start.longitude,
                    end.latitude,
                    end.longitude,
                    product=trimmed.product,
                )
                if len(fallback) > len(shape):
                    shape = fallback
                    shape_source = "route"
        return journey_payload(
            trimmed,
            shape,
            segment=(trimmed.stops[0], trimmed.stops[-1]),
            shape_source=shape_source,
        )

    def services(self, origin: str, destination: str, travel_date: date) -> dict:
        """Parsed service list used to autofill the train field."""
        origin_codes, origin_label = self._expand(origin)
        destination_codes, destination_label = self._expand(destination)
        options = self.repo.search_between_sets(origin_codes, destination_codes, travel_date)
        return {
            "from": {"codes": origin_codes, "name": origin_label},
            "to": {"codes": destination_codes, "name": destination_label},
            "date": travel_date.isoformat(),
            "services": [
                {
                    "train": option.train_number,
                    "departure_time": format_time(option.departure),
                    "arrival_time": format_time(option.arrival),
                    "departure_ms": _ms(option.departure),
                    "arrival_ms": _ms(option.arrival),
                    "duration_minutes": option.duration_minutes,
                    "direct": option.is_direct,
                    "data_type": option.data_type.value,
                    "product": option.product,
                    "product_color": option.product_color,
                    "from_code": option.origin_code,
                    "from_name": option.origin_name,
                    "to_code": option.destination_code,
                    "to_name": option.destination_name,
                }
                for option in options
            ],
        }


def make_handler(app: LiveApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ONCFTracker/0.2"

        def log_message(self, fmt: str, *args) -> None:  # quieter console
            if args and str(args[1]).startswith(("4", "5")):
                print(f"[live] {self.address_string()} {fmt % args}")

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError):
                return

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError):
                return

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            if parsed.path in {"/", "/index.html"}:
                self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/morocco.geojson":
                self._send_file(WEB_DIR / "morocco.geojson", "application/geo+json")
                return
            if parsed.path == "/map3d.js":
                self._send_file(WEB_DIR / "map3d.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/mapextra.js":
                self._send_file(WEB_DIR / "mapextra.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/api/health":
                self._send_json(
                    {
                        "ok": True,
                        "stations": len(app.catalog),
                        "today": datetime.now(tz=TZ).date().isoformat(),
                        "timezone": "Africa/Casablanca",
                    }
                )
                return
            if parsed.path == "/api/grids":
                payload = app.grids_status()
                payload["grids"] = app.grids.describe()
                self._send_json(payload)
                return
            if parsed.path == "/api/traffic":
                refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                self._send_json(app.traffic_info(refresh=refresh))
                return
            if parsed.path == "/api/departures":
                refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                try:
                    payload = app.departures(
                        (query.get("station") or [""])[0], refresh=refresh
                    )
                except KeyError as exc:
                    self._send_json({"error": str(exc.args[0] if exc.args else exc)}, 404)
                except Exception as exc:  # keep the UI alive on source failures
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 502)
                else:
                    self._send_json(payload)
                return
            if parsed.path == "/api/sources":
                refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                self._send_json(app.sources(refresh=refresh))
                return
            if parsed.path == "/api/network":
                self._send_json(app.network())
                return
            if parsed.path == "/api/verify":
                train = (query.get("train") or [""])[0]
                station = (query.get("station") or [""])[0]
                time_text = (query.get("time") or [""])[0]
                result = app.grids.verify(train, station, time_text)
                self._send_json(
                    {
                        "train": result.train,
                        "station": result.station,
                        "printed": result.printed,
                        "status": result.status,
                        "source": result.source,
                        "effective": result.effective,
                        "sheet": result.sheet,
                    }
                )
                return
            if parsed.path == "/api/places":
                text = (query.get("q") or [""])[0]
                city_limit = 40 if text else 200
                station_limit = 40 if text else 200
                cities = app.cities.search(text, limit=city_limit) if text else sorted(
                    app.cities.search("", limit=500, include_empty=True),
                    key=lambda city: (not city.station_codes, -len(city.station_codes), city.name_fr),
                )[:city_limit]
                stations = (
                    app.catalog.search(text, limit=station_limit)
                    if text
                    else app.catalog.all()[:station_limit]
                )
                places = []
                for city in cities:
                    names = [
                        app.catalog.maybe_get(code).display_name
                        for code in city.station_codes
                        if app.catalog.maybe_get(code)
                    ]
                    places.append(
                        {
                            "type": "city",
                            "value": city.key,
                            "code": city.code,
                            "name": city.name_fr,
                            "ar": city.name_ar,
                            "station_count": len(city.station_codes),
                            "stations": names[:6],
                            "disabled": not city.station_codes,
                        }
                    )
                for station in stations:
                    city = app.cities.maybe_get(station.city_code) if station.city_code else None
                    places.append(
                        {
                            "type": "station",
                            "value": station.code,
                            "code": station.code,
                            "name": station.name_fr,
                            "ar": station.name_ar,
                            "city": city.name_fr if city else None,
                        }
                    )
                self._send_json({"places": places})
                return
            if parsed.path == "/api/stations":
                text = (query.get("q") or [""])[0]
                try:
                    limit = max(1, min(300, int((query.get("limit") or ["15"])[0])))
                except ValueError:
                    limit = 15
                matches = app.catalog.search(text, limit=limit) if text else app.catalog.all()[:limit]
                self._send_json(
                    {
                        "stations": [
                            {
                                "code": s.code,
                                "name": s.name_fr,
                                "ar": s.name_ar,
                                "en": s.name_en,
                                "lat": s.latitude,
                                "lon": s.longitude,
                            }
                            for s in matches
                        ]
                    }
                )
                return
            if parsed.path == "/api/services":
                try:
                    travel_date = date.fromisoformat((query.get("date") or [date.today().isoformat()])[0])
                    payload = app.services(
                        (query.get("from") or [""])[0],
                        (query.get("to") or [""])[0],
                        travel_date,
                    )
                except KeyError as exc:
                    self._send_json({"error": str(exc.args[0] if exc.args else exc)}, 404)
                except Exception as exc:
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 502)
                else:
                    self._send_json(payload)
                return
            if parsed.path == "/api/grids/refresh":
                started = app.start_grid_refresh(force=True)
                self._send_json({"started": started, "status": app.grids_status()})
                return
            if parsed.path == "/api/journey":
                try:
                    travel_date = date.fromisoformat((query.get("date") or [date.today().isoformat()])[0])
                    payload = app.journey(
                        (query.get("train") or [""])[0],
                        (query.get("from") or [""])[0],
                        (query.get("to") or [""])[0],
                        travel_date,
                        (query.get("official") or [None])[0],
                        (query.get("verify_all") or ["0"])[0].lower() in {"1", "true", "yes"},
                    )
                except KeyError as exc:
                    self._send_json({"error": str(exc.args[0] if exc.args else exc)}, 404)
                except LookupError as exc:
                    self._send_json({"error": str(exc)}, 404)
                except Exception as exc:  # keep the UI alive on source failures
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 502)
                else:
                    self._send_json(payload)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

    return Handler


def serve(
    port: int = 8767,
    *,
    offline: bool = False,
    open_browser: bool = True,
    query: str = "",
    min_interval: float = 1.0,
    gtfs_dir: Path | None = None,
) -> None:
    ensure_dirs()
    app = LiveApp(offline=offline, min_interval=min_interval, gtfs_dir=gtfs_dir)
    app.start_grid_refresh()

    def grid_refresh_scheduler() -> None:
        while True:
            time.sleep(3600)
            app.start_grid_refresh()

    threading.Thread(
        target=grid_refresh_scheduler,
        name="oncf-grid-scheduler",
        daemon=True,
    ).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/{('?' + query) if query else ''}"
    print(f"ONCF live map: {url}")
    print("Press Ctrl+C to stop. Binds to 127.0.0.1 only.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def summary_line(journey: Journey) -> str:
    origin = journey.origin.station.name_fr if journey.origin else "?"
    destination = journey.destination.station.name_fr if journey.destination else "?"
    first = journey.stops[0].shown_departure if journey.stops else None
    return f"{journey.service_label}: {origin} {format_time(first)} → {destination}"



