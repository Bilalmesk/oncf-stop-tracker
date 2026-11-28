"""Command-line interface for the ONCF stop tracker."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .api import OfficialOncfClient
from .config import (
    AUTO_GRID_DIR,
    CACHE_DIR,
    DATA_DIR,
    GRID_DIR,
    GTFS_DIR,
    ONCF_BASE_URL,
    SAMPLE_DIR,
    ensure_dirs,
)
from .grid import GridLibrary
from .gtfs import GtfsFeed
from .http import HttpClient
from .mobile import LiveDeparturesService, MobileOncfClient
from .models import DataType, Journey
from .repository import Repository, trim_to_segment
from .serialization import journey_to_dict
from .stations import Station, StationCatalog
from .timeutil import format_time


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _add_date(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", type=_parse_date, default=date.today(), help="travel date (YYYY-MM-DD)")


def _add_route(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="origin", required=True, help="origin station code or name")
    parser.add_argument("--to", dest="destination", required=True, help="destination station code or name")
    _add_date(parser)


def _catalog(args: argparse.Namespace) -> StationCatalog:
    client = HttpClient(ONCF_BASE_URL)
    allow_network = not getattr(args, "offline", False)
    return StationCatalog.load(client, allow_network=allow_network)


def _repo(args: argparse.Namespace, catalog: StationCatalog) -> Repository:
    offline = getattr(args, "offline", False)
    source = getattr(args, "source", "auto")

    official = None if offline else OfficialOncfClient(
        use_cache=True, min_interval=getattr(args, "min_interval", 1.0)
    )

    gtfs = None
    gtfs_dir = Path(getattr(args, "gtfs_dir", GTFS_DIR))
    if gtfs_dir.exists():
        gtfs = GtfsFeed(gtfs_dir)

    if gtfs is None and official is None:
        raise SystemExit("No data source available: need the GTFS directory or network access.")

    automatic = GridLibrary.load_dir(AUTO_GRID_DIR)
    grids = automatic if len(automatic) else GridLibrary.load_dir(GRID_DIR)
    return Repository(catalog, official=official, gtfs=gtfs, grids=grids)


def _journey_for_stations(
    repo: Repository,
    args: argparse.Namespace,
    origin: Station,
    destination: Station,
) -> Journey:
    """Use the same full stop-list path as the web server.

    ``repo.journey`` only knows GTFS and the two-station official answer. The
    grid PDFs (including auto-current) are used by ``journey_between_sets``, so
    the CLI must take that path or it will silently show only origin/destination.
    """
    if args.source == "official":
        return repo.journey(
            args.train, origin.code, destination.code, args.date, source="official"
        )
    if args.source == "gtfs":
        return repo.journey(
            args.train, origin.code, destination.code, args.date, source="gtfs"
        )
    full = repo.journey_between_sets(
        args.train,
        [origin.code],
        [destination.code],
        args.date,
    )
    return trim_to_segment(full, [origin.code], [destination.code])


def _print_provenance(journey: Journey) -> None:
    if journey.provenance:
        print(f"Source: {journey.provenance.describe()}")
    for note in journey.notes:
        print(f"Note:   {note}")


def cmd_stations(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    stations = catalog.search(args.query, limit=args.limit) if args.query else catalog.all()[: args.limit]
    for station in stations:
        print(f"{station.code:>4}  {station.name_fr:<28} {station.name_ar or '':<24} {station.name_en or ''}")
    print(f"\n{len(stations)} station(s) shown · catalog of {len(catalog)} · {catalog.source}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    origin = catalog.resolve(args.origin)
    destination = catalog.resolve(args.destination)
    repo = _repo(args, catalog)

    options = repo.search(origin.code, destination.code, args.date, prefer=args.source)
    print(f"{origin.name_fr} → {destination.name_fr}  ·  {args.date.isoformat()}\n")
    if not options:
        print("No service found in the selected source.")
        return 1

    print(f"{'train':<8} {'depart':>7} {'arrive':>7} {'dur.':>7}  {'type':<12} source")
    for option in options:
        duration = f"{option.duration_minutes} min" if option.duration_minutes is not None else "--"
        kind = "direct" if option.is_direct else "connection"
        print(
            f"{option.train_number:<8} {format_time(option.departure):>7} {format_time(option.arrival):>7} "
            f"{duration:>7}  {kind:<12} {option.data_type.value}"
        )
    if args.json:
        print(json.dumps([o.__dict__ | {"departure": str(o.departure), "arrival": str(o.arrival), "data_type": o.data_type.value} for o in options], ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_departures(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    station = catalog.resolve(args.station)
    client = None if getattr(args, "offline", False) else MobileOncfClient(
        min_interval=getattr(args, "min_interval", 1.0)
    )
    service = LiveDeparturesService(client, catalog)
    payload = service.get(station.code, refresh=args.refresh)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"{station.name_fr} ({station.code}) — live departures")
    print(f"Source:  {payload['source']}")
    if payload.get("fetched_at"):
        print(f"Fetched: {payload['fetched_at']}")
    if payload.get("stale"):
        note = "cached result"
        if payload.get("error"):
            note += f" · {payload['error']}"
        print(f"Note:    {note}")
    if not payload.get("departures"):
        if payload.get("error"):
            print(f"Unavailable: {payload['error']}")
            return 1
        print("No upcoming departures in the next hour.")
        print(payload.get("note") or "")
        return 0

    print()
    print(f"{'train':<8} {'sched':>8} {'real':>8} {'delay':>7}  {'level':<7} {'destination':<24} product")
    for item in payload["departures"]:
        delay = f"+{item['delay_minutes']}m" if item.get("delay_minutes") else "on time"
        destination = item.get("destination_name") or item.get("destination_code") or "—"
        print(
            f"{item['train']:<8} {item['scheduled']:>8} {item['real']:>8} {delay:>7}  "
            f"{item['delay_label']:<7} {destination[:24]:<24} {item.get('product') or ''}"
        )
    print()
    print(payload.get("note") or "")
    return 0


def cmd_stops(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    origin = catalog.resolve(args.origin)
    destination = catalog.resolve(args.destination)
    repo = _repo(args, catalog)

    try:
        journey = _journey_for_stations(repo, args, origin, destination)
    except LookupError as exc:
        print(f"Not available: {exc}")
        return 2
    if getattr(args, "verify_all", False) and repo.official is not None:
        journey = repo.hydrate_all_stops(journey)
    if args.json:
        print(json.dumps(journey_to_dict(journey), ensure_ascii=False, indent=2))
        return 0

    print(f"Train {journey.service_label}  ·  {origin.name_fr} → {destination.name_fr}  ·  {args.date.isoformat()}\n")
    print(f"{'#':>3}  {'station':<26} {'arrive':>7} {'depart':>7} {'stop':>15}  status")
    for stop in journey.stops:
        print(
            f"{stop.sequence:>3}  {stop.station.display_name:<26} "
            f"{format_time(stop.shown_arrival):>7} {format_time(stop.shown_departure):>7} "
            f"{stop.duration_label:>15}  {stop.status.value}"
        )
    print()
    _print_provenance(journey)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    catalog = _catalog(args)
    origin = catalog.resolve(args.origin)
    destination = catalog.resolve(args.destination)
    station = catalog.resolve(args.station)
    repo = _repo(args, catalog)

    try:
        journey = _journey_for_stations(repo, args, origin, destination)
    except LookupError as exc:
        print(f"Not available: {exc}")
        return 2

    if args.official and repo.official is not None:
        journey = repo.hydrate_stop(
            journey, station.code, origin_code=origin.code, destination_code=destination.code
        )

    stop = journey.stop_for(station.code)
    if stop is None:
        print(f"Train {args.train} does not serve {station.name_fr} on {args.date}.")
        return 1

    if args.json:
        payload = journey_to_dict(journey)
        print(json.dumps([s for s in payload["stops"] if s["station_id"] == station.code][0], ensure_ascii=False, indent=2))
        return 0

    print(f"Station:   {station.name_fr} ({station.code})")
    print(f"Train:     {journey.service_label}")
    print(f"Date:      {args.date.isoformat()}  ({journey.timezone})")
    print(f"Arrival:   {format_time(stop.shown_arrival)}")
    print(f"Departure: {format_time(stop.shown_departure)}")
    print(f"Stop:      {stop.duration_label}")
    print(f"Kind:      {stop.kind.value}")
    print(f"Status:    {stop.status.label}")
    if journey.provenance:
        print(f"Source:    {journey.provenance.describe()}")
    if args.verify:
        _pdf_check(journey.service_label, station.name_fr, stop, args.pdf_dir)
    if stop.status is DataType.SCHEDULED_UNOFFICIAL:
        print(
            "\nWarning: these times come from the unofficial GTFS mirror. Re-run with "
            "--official to fetch ONCF's own answer for this station."
        )
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_absolute():
        path = SAMPLE_DIR / path
    from .serialization import journey_from_dict

    journey = journey_from_dict(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps(journey_to_dict(journey), ensure_ascii=False, indent=2))
    return 0


def _pdf_check(train: str, station_name: str, stop, pdf_dir: str) -> None:
    """Compare the displayed time with ONCF's official printed grid."""
    from .grid import GridLibrary
    from .timeutil import format_time

    moment = stop.shown_departure
    if moment is None:
        return
    library = GridLibrary.load_dir(pdf_dir)
    if not len(library):
        return
    clock = format_time(moment)
    result = library.verify(train, station_name, clock)
    if result.status == "match":
        print(f"PDF check: ✓ {result.printed} matches {result.source} (effective {result.effective})")
    elif result.status == "mismatch":
        print(f"PDF check: ✗ {result.source} prints {result.printed}, app shows {clock}")
    else:
        print("PDF check: no coverage for this train/station in the downloaded grids")


def _grid_library(directory: str | Path):
    from .grid import GridLibrary

    path = Path(directory)
    if path == Path(GRID_DIR):
        automatic = GridLibrary.load_dir(AUTO_GRID_DIR)
        if len(automatic):
            return automatic
    return GridLibrary.load_dir(path)


def cmd_grid(args: argparse.Namespace) -> int:
    library = _grid_library(args.dir)
    if not len(library):
        print(f"No parseable grid PDFs in {args.dir}")
        return 1
    for info in library.describe():
        print(f"{info['file']}  ·  effective {info['effective'] or '?'}  ·  {info['sections']} sections")
        print(f"  {info['title']}")
        print(f"  trains: {', '.join(info['trains'])}")
    return 0


def cmd_identify(args: argparse.Namespace) -> int:
    library = _grid_library(args.dir)
    matches = library.identify(args.station, args.time, tolerance_minutes=args.tolerance)
    if not matches:
        print(f"No printed train at {args.station} around {args.time}.")
        print("Check the grid's effective date — an older PDF will not match current times.")
        return 1
    print(f"{'train':<8} {'station':<24} {'printed':>7}  source")
    for match in matches:
        print(f"{match.train:<8} {match.station:<24} {match.printed:>7}  {match.source} ({match.effective})")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    from .live import serve

    query = ""
    if args.train and args.origin and args.destination:
        params = {
            "train": args.train,
            "from": args.origin,
            "to": args.destination,
            "date": args.date.isoformat(),
            "official": args.official_station or "",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
    serve(
        port=args.port,
        offline=getattr(args, "offline", False),
        open_browser=not args.no_browser,
        query=query,
        min_interval=getattr(args, "min_interval", 1.0),
        gtfs_dir=Path(args.gtfs_dir),
    )
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    from .sources import probe_sources

    data = probe_sources()
    for check in data.get("checks", []):
        state = "online" if check.get("ok") else "offline"
        http = check.get("http")
        detail = f"{state:7}  {check['name']} ({check['host']})"
        if http is not None:
            detail += f"  HTTP {http}"
        print(detail)
        if check.get("note"):
            print(f"          {check['note']}")
    print(data.get("note", ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oncf",
        description="ONCF train stop tracker: scheduled arrival/departure and stop duration.",
    )
    parser.add_argument("--offline", action="store_true", help="use only cached/local data, no network")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--min-interval", type=float, default=1.0, help="seconds between ONCF requests")
    parser.add_argument(
        "--gtfs-dir",
        default=str(GTFS_DIR),
        help="GTFS feed directory (default: data/orhazal, or ONCF_GTFS_DIR)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stations", help="search the official station catalogue")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_stations)

    p = sub.add_parser("search", help="list services between two stations")
    _add_route(p)
    p.add_argument("--source", choices=["auto", "official", "gtfs"], default="auto")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("departures", help="live departures and delays for one station")
    p.add_argument("--station", required=True, help="station code or name")
    p.add_argument("--refresh", action="store_true", help="bypass the 45 s cache")
    p.set_defaults(func=cmd_departures)

    p = sub.add_parser("stops", help="show the ordered stop list of a train")
    _add_route(p)
    p.add_argument("--train", required=True, help="commercial train number, e.g. MA or 103")
    p.add_argument("--source", choices=["auto", "gtfs", "official"], default="auto")
    p.add_argument("--verify-all", action="store_true",
                   help="fetch exact arrival, departure and dwell for every stop from ONCF")
    p.set_defaults(func=cmd_stops)

    p = sub.add_parser("stop", help="details for one station, optionally hydrated from ONCF")
    _add_route(p)
    p.add_argument("--train", required=True)
    p.add_argument("--station", required=True, help="station code or name")
    p.add_argument("--source", choices=["auto", "gtfs", "official"], default="auto")
    p.add_argument("--official", action="store_true", help="fetch authoritative times from ONCF")
    p.add_argument("--verify", dest="verify", action="store_true", default=True,
                   help="cross-check against official grid PDFs (default)")
    p.add_argument("--no-verify", dest="verify", action="store_false")
    p.add_argument("--pdf-dir", default=str(GRID_DIR), help="directory of official grid PDFs")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("grid", help="parse and summarise official grid PDFs")
    p.add_argument("--dir", default=str(GRID_DIR))
    p.set_defaults(func=cmd_grid)

    p = sub.add_parser("identify", help="find the train number printed at a station and time")
    p.add_argument("--station", required=True)
    p.add_argument("--time", required=True, help="printed time, HH:MM")
    p.add_argument("--dir", default=str(GRID_DIR))
    p.add_argument("--tolerance", type=int, default=2, help="minutes of tolerance")
    p.set_defaults(func=cmd_identify)

    p = sub.add_parser("sample", help="print a bundled normalized journey file")
    p.add_argument("path")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("sources", help="check which ONCF sources are reachable")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("live", help="serve the local live map (your GPS + scheduled estimate)")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--train", help="optional: open straight into this train")
    p.add_argument("--from", dest="origin", help="optional origin station")
    p.add_argument("--to", dest="destination", help="optional destination station")
    _add_date(p)
    p.add_argument("--official-station", default="", help="station to refresh from ONCF on load")
    p.set_defaults(func=cmd_live)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    args_list = list(sys.argv[1:] if argv is None else argv)

    # Windows CMD does not use "\" for line continuation. People paste bash
    # examples and get a confusing argparse error, so strip the stray token and
    # explain instead of failing.
    stray = [token for token in args_list if token in {"\\", "^"}]
    if stray:
        args_list = [token for token in args_list if token not in {"\\", "^"}]
        print(
            "Note: '\\' is a bash line-continuation character; Windows CMD does not "
            "use it. The command was run as a single line.",
            file=sys.stderr,
        )

    parser = build_parser()
    args = parser.parse_args(args_list)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"Station error: {exc.args[0] if exc.args else exc}")
        return 2
    except Exception as exc:  # network/source failures should not print a traceback
        from .http import OncfApiError

        if isinstance(exc, OncfApiError):
            print(f"ONCF source error: {exc}")
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())


