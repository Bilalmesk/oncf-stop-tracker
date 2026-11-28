"""Display-only cleanups for the community GTFS feed.

The feed is the source of station identity and stop sequences. Its display
names contain a handful of typos and unaccented French names, plus Supratours
markers such as ``(suprat.)``. These helpers change **labels only**; they never
change a station code, stop id or trip.
"""
from __future__ import annotations

import re

# Exact, case-insensitive display fixes. Keep this list small and evidence-based.
_STATION_NAMES = {
    "aeroport med v": "Aéroport Mohammed V",
    "ain-taoujdate": "Aïn Taoujdate",
    "beni-oukil": "Beni Oukil",
    "sebaa-aioun": "Sebaa Aioun",
    "facultes": "Facultés",
    "fes": "Fès",
    "kenitra": "Kénitra",
    "meknes": "Meknès",
    "sale": "Salé",
}

_SUPRAT_RE = re.compile(r"\(\s*supra(?:t(?:ours)?)?\.?\s*\)", re.IGNORECASE)
_SPACES_RE = re.compile(r"\s{2,}")


def clean_station_name(name: str | None) -> str:
    """Return a display name with known typos/accents fixed."""
    text = str(name or "").strip()
    if not text:
        return ""
    return _STATION_NAMES.get(text.casefold(), text)


def clean_route_name(name: str | None) -> str:
    """Remove Supratours markers and tidy whitespace in a route label."""
    text = str(name or "").strip()
    if not text:
        return ""
    cleaned = _SUPRAT_RE.sub("", text)
    cleaned = _SPACES_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+-\s+", " - ", cleaned).strip(" -")
    return cleaned or text
