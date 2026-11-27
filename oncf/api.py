"""Client for the official ONCF timetable gateway.

Endpoint (discovered in the public frontend bundle, no authentication):

    POST {ONCF_BASE_URL}/api/schedule

It answers "which services run from A to B on date D", returning each service's
departure and arrival and its commercial train number (``numeroCommercial``,
e.g. ``MA``, ``103``). It does **not** return intermediate stops in one call:
a leg is only reported between the two stations you asked for. Stop-level times
are therefore assembled by querying the two sub-legs of a station (see
``repository.py``).

The payload must be complete. Omitting fields makes the gateway answer
``errorCode 990 / Problème technique`` rather than a useful validation message.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import CACHE_DIR, MIN_REQUEST_INTERVAL, ONCF_BASE_URL, SCHEDULE_CACHE_TTL_HOURS
from .http import HttpClient, OncfApiError
from .timeutil import TZ, parse_local

SCHEDULE_PATH = "/api/schedule"


@dataclass(frozen=True)
class Segment:
    origin_code: str
    destination_code: str
    departure: datetime | None
    arrival: datetime | None
    train_number: str
    product_code: str | None = None
    comfort: str | None = None


@dataclass
class Leg:
    """One journey option between the two queried stations."""

    origin_code: str
    destination_code: str
    departure: datetime | None
    arrival: datetime | None
    segments: list[Segment] = field(default_factory=list)

    @property
    def is_direct(self) -> bool:
        return len(self.segments) == 1

    @property
    def product_code(self) -> str | None:
        return self.segments[0].product_code if self.segments else None

    @property
    def train_numbers(self) -> list[str]:
        return [s.train_number for s in self.segments]

    @property
    def primary_train(self) -> str | None:
        return self.segments[0].train_number if self.segments else None


def _build_payload(dep_code: str, arr_code: str, travel_date: date) -> dict:
    return {
        "codeGareDepart": dep_code,
        "codeGareArrivee": arr_code,
        # 2 = second class; the timetable is identical in both classes.
        "codeNiveauConfort": 2,
        "dateDepartAller": f"{travel_date.isoformat()}T00:01:50+00:00",
        "dateDepartAllerMax": None,
        "dateDepartRetour": None,
        "dateDepartRetourMax": None,
        "isTrainDirect": None,
        "isPreviousTrainAller": None,
        "isTarifReduit": True,
        "adulte": 1,
        "kids": 0,
        "listVoyageur": [
            {
                "numeroClient": None,
                "codeTarif": None,
                "codeProfilDemographique": "3",
                "dateNaissance": None,
            }
        ],
        "booking": False,
        "isEntreprise": False,
        "token": "",
        "numeroContract": "",
        "codeTiers": "",
        "iTravel": False,
        "isActive": False,
    }


def _parse_legs(body: dict) -> list[Leg]:
    legs: list[Leg] = []
    for path in body.get("departurePath") or []:
        segments = [
            Segment(
                origin_code=str(seg.get("codeGareDepart") or ""),
                destination_code=str(seg.get("codeGareArrivee") or ""),
                departure=parse_local(seg.get("dateHeureDepart")),
                arrival=parse_local(seg.get("dateHeureArrivee")),
                train_number=str(seg.get("numeroCommercial") or "").strip(),
                product_code=str(seg.get("codeGamme") or "") or None,
                comfort=str(seg.get("codeNiveauConfort") or "") or None,
            )
            for seg in (path.get("listSegments") or [])
        ]
        legs.append(
            Leg(
                origin_code=str(path.get("codeGareDepart") or ""),
                destination_code=str(path.get("codeGareArrivee") or ""),
                departure=parse_local(path.get("dateTimeDepart")),
                arrival=parse_local(path.get("dateTimeArrivee")),
                segments=segments,
            )
        )
    return legs


class OfficialOncfClient:
    """Thin, cached, rate-limited wrapper around ``/api/schedule``."""

    def __init__(
        self,
        base_url: str = ONCF_BASE_URL,
        *,
        cache_dir: Path | None = None,
        min_interval: float = MIN_REQUEST_INTERVAL,
        use_cache: bool = True,
        cache_ttl_hours: float = SCHEDULE_CACHE_TTL_HOURS,
    ):
        self.http = HttpClient(base_url, min_interval=min_interval)
        self.cache_dir = cache_dir or CACHE_DIR
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_hours * 3600
        self.last_retrieved_at: datetime | None = None

    # --- cache ----------------------------------------------------------
    def _cache_file(self, dep: str, arr: str, travel_date: date) -> Path:
        return self.cache_dir / f"schedule_{dep}_{arr}_{travel_date.isoformat()}.json"

    def _read_cache(self, path: Path) -> dict | None:
        if not self.use_cache or not path.exists():
            return None
        if self.cache_ttl_seconds >= 0 and time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # --- public API -----------------------------------------------------
    def schedule(self, dep_code: str, arr_code: str, travel_date: date, *, force: bool = False) -> list[Leg]:
        cache_file = self._cache_file(dep_code, arr_code, travel_date)
        raw = None if force else self._read_cache(cache_file)

        if raw is None:
            raw = self.http.request_json(
                "POST",
                SCHEDULE_PATH,
                json=_build_payload(dep_code, arr_code, travel_date),
                headers={
                    "Origin": self.http.base_url,
                    "Referer": f"{self.http.base_url}/recherche-horaires",
                },
            )
            head = raw.get("head") or {}
            if str(head.get("errorCode")) not in {"0", ""}:
                raise OncfApiError(
                    f"ONCF returned errorCode={head.get('errorCode')}: "
                    f"{head.get('errorMessage') or head.get('errorMessage_En')}"
                )
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        self.last_retrieved_at = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=TZ)
        return _parse_legs(raw.get("body") or {})

    def direct_leg(self, dep_code: str, arr_code: str, travel_date: date, train_number: str) -> Leg | None:
        """Return the direct leg operated by ``train_number``, if it runs."""
        for leg in self.schedule(dep_code, arr_code, travel_date):
            if leg.is_direct and leg.primary_train == train_number:
                return leg
        return None

    def train_numbers(self, dep_code: str, arr_code: str, travel_date: date) -> list[str]:
        seen: list[str] = []
        for leg in self.schedule(dep_code, arr_code, travel_date):
            for number in leg.train_numbers:
                if number and number not in seen:
                    seen.append(number)
        return seen
