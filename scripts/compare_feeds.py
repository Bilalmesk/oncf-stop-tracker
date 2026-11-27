"""Compare two GTFS feeds (provider due diligence).

    python scripts/compare_feeds.py data/orhazal data/transitdim

Reports coverage, whether train numbers and platform codes are usable, and
which services exist in one feed but not the other. Useful before switching
providers with ``--gtfs-dir``.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path


def read(directory: Path, name: str) -> list[dict]:
    path = directory / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def feed_info(directory: Path) -> dict:
    rows = read(directory, "feed_info.txt")
    return rows[0] if rows else {}


def summarize(directory: Path) -> dict:
    stops = read(directory, "stops.txt")
    trips = read(directory, "trips.txt")
    stop_times = read(directory, "stop_times.txt")
    info = feed_info(directory)
    return {
        "name": directory.name,
        "publisher": info.get("feed_publisher_name"),
        "version": info.get("feed_version"),
        "start": info.get("feed_start_date"),
        "end": info.get("feed_end_date"),
        "stops": len(stops),
        "stations": sum(1 for s in stops if s.get("location_type") == "1"),
        "platform_codes": sum(1 for s in stops if (s.get("platform_code") or "").strip()),
        "pathways": len(read(directory, "pathways.txt")),
        "levels": len(read(directory, "levels.txt")),
        "trips": len(trips),
        "stop_times": len(stop_times),
        "train_numbers": sum(1 for t in trips if (t.get("trip_short_name") or "").strip()),
        "trains": {t["trip_short_name"] for t in trips if (t.get("trip_short_name") or "").strip()},
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    left = Path(argv[0])
    right = Path(argv[1])
    a, b = summarize(left), summarize(right)

    print(f"{'metric':<22}{a['name']:>18}{b['name']:>18}")
    print("-" * 58)
    for key in (
        "publisher", "version", "start", "end", "stops", "stations",
        "platform_codes", "pathways", "levels", "trips", "stop_times", "train_numbers",
    ):
        print(f"{key:<22}{str(a[key]):>18}{str(b[key]):>18}")

    only_a = sorted(a["trains"] - b["trains"])
    only_b = sorted(b["trains"] - a["trains"])
    print(f"\ntrains only in {a['name']}: {', '.join(only_a[:30]) or '-'}")
    print(f"trains only in {b['name']}: {', '.join(only_b[:30]) or '-'}")

    if not a["platform_codes"] and not b["platform_codes"]:
        print("\nNeither feed carries platform/voie codes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
