"""City catalogue: groups ONCF stations into the cities they serve.

ONCF sells tickets between *cities*, but the timetable gateway only answers
between individual stations. Casablanca, for example, has seven stations
(`CASA VOYAGEURS`, `CASA PORT`, `CASA OASIS`, `AIN SEBAA`, …) and Rabat has
three. A city-level search therefore expands to every station of both cities and
merges the results, keeping the specific station on each option.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CACHE_DIR, STATION_CACHE_TTL_HOURS
from .http import HttpClient
from .models import Station


@dataclass(frozen=True)
class City:
    code: str
    name_fr: str
    name_ar: str | None = None
    name_en: str | None = None
    station_codes: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return self.name_fr

    def matches(self, query: str) -> bool:
        q = query.strip().casefold()
        if not q:
            return True
        return any(q in (v or "").casefold() for v in (self.name_fr, self.name_ar, self.name_en, self.code))

    @property
    def key(self) -> str:
        return f"c:{self.code}"


class CityCatalog:
    def __init__(self, cities: dict[str, City]):
        self._cities = cities

    # --- construction ---------------------------------------------------
    @classmethod
    def from_payload(cls, raw: dict, stations: dict[str, Station]) -> "CityCatalog":
        by_city: dict[str, list[str]] = {}
        for station in stations.values():
            if station.city_code:
                by_city.setdefault(station.city_code, []).append(station.code)

        cities: dict[str, City] = {}
        for item in raw.get("cities", []):
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            cities[code] = City(
                code=code,
                name_fr=(item.get("designationFr") or "").strip(),
                name_ar=(item.get("designationAr") or "").strip() or None,
                name_en=(item.get("designationEn") or "").strip() or None,
                station_codes=tuple(sorted(by_city.get(code, []))),
            )
        return cls(cities)

    @classmethod
    def load(cls, client: HttpClient, stations: dict[str, Station], *, cache_path: Path | None = None) -> "CityCatalog":
        cache_path = cache_path or (CACHE_DIR / "cities.json")
        fresh = cache_path.exists() and (
            time.time() - cache_path.stat().st_mtime < STATION_CACHE_TTL_HOURS * 3600
        )
        if fresh:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            raw = client.request_json("GET", "/cache/cities")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return cls.from_payload(raw, stations)

    # --- access ---------------------------------------------------------
    def get(self, code: str) -> City:
        key = str(code).removeprefix("c:").strip()
        if key not in self._cities:
            raise KeyError(f"unknown city code: {code}")
        return self._cities[key]

    def maybe_get(self, code: str) -> City | None:
        return self._cities.get(str(code).removeprefix("c:").strip())

    def search(self, query: str, limit: int = 10, *, include_empty: bool = False) -> list[City]:
        q = query.strip()
        matches = [
            c
            for c in self._cities.values()
            if c.matches(q) and (include_empty or c.station_codes)
        ]
        matches.sort(key=lambda c: (not c.name_fr.casefold().startswith(q.casefold()), c.name_fr))
        return matches[:limit]

    def resolve(self, value: str) -> City:
        key = str(value).removeprefix("c:").strip()
        if key in self._cities:
            return self._cities[key]
        folded = key.casefold()
        exact = [
            c
            for c in self._cities.values()
            if folded in {c.name_fr.casefold(), (c.name_ar or "").casefold(), (c.name_en or "").casefold()}
        ]
        if len(exact) == 1:
            return exact[0]
        matches = self.search(key)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"no city matches {value!r}")
        options = ", ".join(f"{c.code}={c.name_fr}" for c in matches[:6])
        raise KeyError(f"{value!r} is ambiguous: {options}")

    def __len__(self) -> int:
        return len(self._cities)
