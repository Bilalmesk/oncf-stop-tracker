"""Filesystem locations and runtime settings."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DATA_DIR = Path(os.environ.get("ONCF_DATA_DIR", PROJECT_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
OFFICIAL_DIR = DATA_DIR / "official"
SAMPLE_DIR = DATA_DIR / "sample"
GRID_DIR = DATA_DIR / "pdf" / "official-2026-10"
AUTO_GRID_DIR = DATA_DIR / "pdf" / "auto-current"
GRID_REFRESH_HOURS = float(os.environ.get("ONCF_GRID_REFRESH_HOURS", "24"))
GTFS_DIR = Path(os.environ.get("ONCF_GTFS_DIR", DATA_DIR / "orhazal"))

ONCF_BASE_URL = os.environ.get("ONCF_BASE_URL", "https://shortora.duckdns.org/oncf")
ONCF_MOBILE_BASE = os.environ.get("ONCF_MOBILE_BASE", ONCF_BASE_URL)
ONCF_TIMEZONE = "Africa/Casablanca"

# Reference data changes rarely; timetables change with the travel date.
STATION_CACHE_TTL_HOURS = 24 * 7
SCHEDULE_CACHE_TTL_HOURS = float(os.environ.get("ONCF_CACHE_TTL_HOURS", "6"))
MIN_REQUEST_INTERVAL = float(os.environ.get("ONCF_MIN_INTERVAL", "1.0"))


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, OFFICIAL_DIR, SAMPLE_DIR):
        path.mkdir(parents=True, exist_ok=True)
