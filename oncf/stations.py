"""Official ONCF station catalogue (``GET /cache/stations``).

This is the closest thing ONCF publishes to a station reference list: stable
``codeGare`` values, French/Arabic/English names and coordinates. The same codes
appear as ``stop_id`` values in the community GTFS feed, which is why the two
sources can be joined safely.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import CACHE_DIR, STATION_CACHE_TTL_HOURS
from .http import HttpClient
from .models import Station


class StationCatalog:
    def __init__(self, stations: dict[str, Station], source: str, retrieved_at: float):
        self._stations = stations
        self.source = source
        self.retrieved_at = retrieved_at

    # --- construction ---------------------------------------------------
    @classmethod
    def from_file(cls, path: Path) -> "StationCatalog":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(cls._parse(raw), f"file:{path.name}", path.stat().st_mtime)

    @classmethod
    def load(cls, client: HttpClient, *, cache_path: Path | None = None, allow_network: bool = True) -> "StationCatalog":
        cache_path = cache_path or (CACHE_DIR / "stations.json")
        fresh = cache_path.exists() and (
            time.time() - cache_path.stat().st_mtime < STATION_CACHE_TTL_HOURS * 3600
        )
        if fresh:
            return cls.from_file(cache_path)
        if allow_network:
            raw = client.request_json("GET", "/cache/stations")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            return cls(cls._parse(raw), "station catalogue cache", time.time())
        if cache_path.exists():
            return cls.from_file(cache_path)
        raise FileNotFoundError(
            "No cached station list and network access is disabled. "
            "Run with network once, or copy data/official/cache_stations.json to data/cache/stations.json."
        )

    @staticmethod
    def _parse(raw: dict) -> dict[str, Station]:
        stations: dict[str, Station] = {}
        for item in raw.get("station", []):
            code = str(item.get("codeGare") or item.get("code") or "").strip()
            if not code:
                continue
            try:
                lat = float(item["latitude"]) if item.get("latitude") else None
                lon = float(item["longitude"]) if item.get("longitude") else None
            except (TypeError, ValueError):
                lat = lon = None
            stations[code] = Station(
                code=code,
                name_fr=(item.get("designationFr") or "").strip(),
                name_ar=(item.get("designationAr") or "").strip() or None,
                name_en=(item.get("designationEn") or "").strip() or None,
                latitude=lat,
                longitude=lon,
                city_code=str(item.get("codeVille") or "") or None,
            )
        return stations

    # --- access ---------------------------------------------------------
    def get(self, code: str) -> Station:
        key = str(code).strip()
        if key not in self._stations:
            raise KeyError(f"unknown ONCF station code: {code}")
        return self._stations[key]

    def maybe_get(self, code: str) -> Station | None:
        return self._stations.get(str(code).strip())

    def search(self, query: str, limit: int = 20) -> list[Station]:
        matches = [s for s in self._stations.values() if s.matches(query)]
        matches.sort(key=lambda s: (not s.name_fr.casefold().startswith(query.strip().casefold()), s.name_fr))
        return matches[:limit]

    def all(self) -> list[Station]:
        return sorted(self._stations.values(), key=lambda s: s.name_fr)

    def index(self) -> dict[str, Station]:
        """Station code → station, for building other catalogues."""
        return dict(self._stations)

    def resolve(self, value: str) -> Station:
        """Accept a station code, an exact name, or a unique name fragment."""
        key = " ".join(str(value).split())
        if key in self._stations:
            return self._stations[key]

        folded = key.casefold()
        if folded:
            exact = [
                station
                for station in self._stations.values()
                if folded
                in {
                    station.name_fr.casefold(),
                    (station.name_ar or "").casefold(),
                    (station.name_en or "").casefold(),
                }
            ]
            if len(exact) == 1:
                return exact[0]

        matches = self.search(key, limit=5)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"no station matches {value!r}")
        options = ", ".join(f"{s.code}={s.name_fr}" for s in matches)
        raise KeyError(f"{value!r} is ambiguous: {options}")

    def __len__(self) -> int:
        return len(self._stations)
