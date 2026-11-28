"""Live data from the ONCF station board, via the configured gateway.

The gateway exposes the public read endpoint used by the official app:
``POST /mobile/DepartingTrains`` with ``{"codeGare": "231"}`` returns a station
board with scheduled and actual departure clock times plus a delay level. No
token, cookie or API key is needed. It is a station board, **not** a train GPS
feed.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ONCF_MOBILE_BASE
from .http import HttpClient
from .products import from_code
from .stations import Station, StationCatalog

MOBILE_BASE = ONCF_MOBILE_BASE
SOURCE = "ONCF live station board — gateway"
NOTE = "Live station board. Not a train position or per-stop estimate."
DELAY_LABELS = {0: "on_time", 1: "low", 2: "medium", 3: "high"}
DEFAULT_TTL = 45.0


class MobileApiError(RuntimeError):
    """Raised when the mobile gateway returns an error envelope."""


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if hours < 0 or not (0 <= minutes <= 59 and 0 <= seconds <= 59):
        return None
    return hours * 3600 + minutes * 60 + seconds


def delay_minutes(scheduled: str | None, real: str | None) -> int:
    """Minutes late, handling midnight rollover (23:22 → 00:05 = +43)."""
    if not scheduled or not real:
        return 0
    start = _seconds(scheduled)
    end = _seconds(real)
    if start is None or end is None:
        return 0
    diff = end - start
    if diff < -12 * 3600:
        diff += 24 * 3600
    return int(round(diff / 60))


def delay_label(level: int | str | None) -> str:
    try:
        clean = max(0, min(3, int(level or 0)))
    except (TypeError, ValueError):
        clean = 0
    return DELAY_LABELS[clean]


def to_api_datetime(value: str | datetime | date) -> str:
    """Normalize to the only format ``/mobile/schedule`` accepts."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if len(text) == 10:
            text += "T00:00:00"
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclass
class Departure:
    train: str
    scheduled: str
    real: str
    delay_minutes: int
    delay_level: int
    destination_code: str
    gamme_code: str
    destination_name: str | None = None
    product: str = ""
    product_color: str = ""

    @property
    def delay_label(self) -> str:
        return delay_label(self.delay_level)

    @property
    def is_delayed(self) -> bool:
        return self.delay_minutes >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "train": self.train,
            "scheduled": self.scheduled,
            "real": self.real,
            "delay_minutes": self.delay_minutes,
            "delay_level": self.delay_level,
            "delay_label": self.delay_label,
            "destination_code": self.destination_code,
            "destination_name": self.destination_name,
            "gamme_code": self.gamme_code,
            "product": self.product,
            "product_color": self.product_color,
        }


def parse_departures(payload: dict[str, Any], catalog: StationCatalog | None = None) -> list[Departure]:
    """Turn a ``DepartingTrains`` response into enriched departures."""
    if not isinstance(payload, dict):
        return []
    head = payload.get("head")
    if not isinstance(head, dict):
        head = {}
    if head.get("errorCode") not in (0, None, "0"):
        raise MobileApiError(
            f"ONCF mobile API error {head.get('errorCode')}: {head.get('errorMessage') or 'request failed'}"
        )
    body = payload.get("body")
    if not isinstance(body, dict):
        body = {}
    trains = body.get("trains")
    if not isinstance(trains, list):
        return []

    departures: list[Departure] = []
    for item in trains:
        if not isinstance(item, dict):
            continue
        train = str(item.get("numTrain") or "").strip()
        scheduled = str(item.get("heureDepart") or "")
        real = str(item.get("realDepartureTime") or scheduled)
        if not train or not (scheduled or real):
            continue
        code = str(item.get("GareFinale") or item.get("codeGareFinale") or "")
        product, color = from_code(item.get("codeGamme"))
        destination: Station | None = catalog.maybe_get(code) if catalog and code else None
        try:
            level = max(0, min(3, int(item.get("departureDelayLevel") or 0)))
        except (TypeError, ValueError):
            level = 0
        departures.append(
            Departure(
                train=train,
                scheduled=scheduled,
                real=real,
                delay_minutes=delay_minutes(scheduled, real),
                delay_level=level,
                destination_code=code,
                destination_name=destination.name_fr if destination else None,
                gamme_code=str(item.get("codeGamme") or ""),
                product=product,
                product_color=color,
            )
        )
    return departures


class MobileOncfClient:
    """Thin client for the public read endpoints of the mobile gateway."""

    def __init__(
        self,
        http: Any | None = None,
        *,
        min_interval: float = 1.0,
        base: str | None = None,
    ) -> None:
        self.base = (base or MOBILE_BASE).rstrip("/")
        self.http = http or HttpClient(self.base, min_interval=min_interval, timeout=25.0)

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        kwargs = {"json": json_body} if method == "POST" else {}
        payload = self.http.request_json(method, path, **kwargs)
        head = payload.get("head") or {}
        if head.get("errorCode") not in (0, None, "0"):
            raise MobileApiError(
                f"ONCF mobile API error {head.get('errorCode')}: "
                f"{head.get('errorMessage') or 'request failed'}"
            )
        return payload

    def departing_trains(self, code_gare: str) -> dict[str, Any]:
        return self._request("POST", "/mobile/DepartingTrains", json_body={"codeGare": str(code_gare)})

    def news(self, lang: str = "fr") -> dict[str, Any]:
        return self._request("GET", f"/media/CMS/{lang}/news")

    def schedule(self, from_code: str, to_code: str, when: Any) -> dict[str, Any]:
        body = {
            "codeGareDepart": str(from_code),
            "codeGareArrivee": str(to_code),
            "codeNiveauConfort": 2,
            "dateDepartAller": to_api_datetime(when),
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
        return self._request("POST", "/mobile/schedule", json_body=body)

    def availability(self, from_code: str, to_code: str, when: Any) -> dict[str, Any]:
        body = {
            "codeGareDepart": str(from_code),
            "codeGareArrivee": str(to_code),
            "codeNiveauConfort": 2,
            "dateDepartAller": to_api_datetime(when),
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
        }
        return self._request("POST", "/mobile/availability", json_body=body)


class LiveDeparturesService:
    """Memory + disk cache around the live station board."""

    def __init__(
        self,
        client: MobileOncfClient | None,
        catalog: StationCatalog,
        *,
        ttl_seconds: float = DEFAULT_TTL,
        cache_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.catalog = catalog
        self.ttl_seconds = max(10.0, float(ttl_seconds))
        self.cache_dir = cache_dir or (DATA_DIR / "cache")
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[datetime, list[Departure]]] = {}

    def _path(self, code: str) -> Path:
        return self.cache_dir / f"departures-{code}.json"

    def _read_disk(self, code: str) -> tuple[datetime, list[Departure], str | None] | None:
        try:
            raw = json.loads(self._path(code).read_text(encoding="utf-8"))
            when = datetime.fromisoformat(raw["fetched_at"])
            allowed = {field.name for field in fields(Departure)}
            departures = [
                Departure(**{key: value for key, value in item.items() if key in allowed})
                for item in raw.get("departures", [])
            ]
            return when, departures, raw.get("error")
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _write_disk(
        self,
        code: str,
        when: datetime,
        departures: list[Departure],
        error: str | None,
    ) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._path(code).write_text(
                json.dumps(
                    {
                        "fetched_at": when.isoformat(),
                        "departures": [d.to_dict() for d in departures],
                        "error": error,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            return

    def _payload(
        self,
        code: str,
        when: datetime,
        departures: list[Departure],
        error: str | None,
        *,
        stale: bool,
    ) -> dict[str, Any]:
        station = self.catalog.maybe_get(code)
        return {
            "station": {
                "code": code,
                "name": station.name_fr if station else code,
                "ar": station.name_ar if station else "",
                "city": None,
            },
            "departures": [d.to_dict() for d in departures],
            "fetched_at": when.isoformat(),
            "stale": stale,
            "error": error,
            "source": SOURCE,
            "note": NOTE,
        }

    def get(self, code: str, refresh: bool = False) -> dict[str, Any]:
        code = str(code).strip()
        with self._lock:
            now = datetime.now(timezone.utc)
            memory = self._memory.get(code)
            age = (now - memory[0]).total_seconds() if memory else None
            if memory and age is not None and age < self.ttl_seconds and not refresh:
                return self._payload(code, memory[0], memory[1], None, stale=False)
            if memory and age is not None and age < 10 and refresh:
                return self._payload(code, memory[0], memory[1], None, stale=False)

            if self.client is None:
                disk = self._read_disk(code)
                if disk:
                    when, departures, _ = disk
                    if (now - when).total_seconds() < self.ttl_seconds:
                        self._memory[code] = (when, departures)
                        return self._payload(code, when, departures, None, stale=False)
                    return self._payload(code, when, departures, "offline mode", stale=True)
                return self._payload(code, now, [], "offline mode", stale=True)

            error: str | None = None
            departures: list[Departure] = []
            when = now
            try:
                payload = self.client.departing_trains(code)
                departures = parse_departures(payload, self.catalog)
                self._memory[code] = (now, departures)
                self._write_disk(code, now, departures, None)
                return self._payload(code, now, departures, None, stale=False)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                error = f"{type(exc).__name__}: {exc}"[:300]

            disk = self._read_disk(code)
            if disk:
                when, departures, _ = disk
                return self._payload(code, when, departures, error, stale=True)
            return self._payload(code, when, [], error, stale=True)
