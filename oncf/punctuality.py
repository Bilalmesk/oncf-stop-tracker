"""Append-only punctuality observations from the live station board.

This is **not** an official ONCF punctuality dataset. It records what this
installation actually saw on the live departure board, so the UI can compute
honest, local KPIs such as on-time percentage and average delay.

One line is appended when a departure is first seen or when its real time
changes. Repeated identical polls do not inflate the sample.
"""
from __future__ import annotations

import json
import statistics
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR

UTC = timezone.utc
ON_TIME_LIMIT = 5
SEVERE_LIMIT = 15


class PunctualityLog:
    def __init__(
        self,
        path: Path | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path or (DATA_DIR / "punctuality" / "observations.jsonl"))
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._lock = threading.Lock()
        self._seen: dict[str, str] = {}
        self._load_seen()

    def _current(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _service_date(observed_at: datetime, scheduled: str) -> str:
        service_date = observed_at.date()
        try:
            parts = [int(part) for part in str(scheduled).split(":")]
            scheduled_seconds = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
        except (IndexError, TypeError, ValueError):
            return service_date.isoformat()
        observed_seconds = observed_at.hour * 3600 + observed_at.minute * 60 + observed_at.second
        delta = scheduled_seconds - observed_seconds
        if delta < -12 * 3600:
            service_date += timedelta(days=1)
        elif delta > 12 * 3600:
            service_date -= timedelta(days=1)
        return service_date.isoformat()

    @classmethod
    def _key(cls, observed_at: datetime, station: str, train: str, scheduled: str) -> str:
        return f"{cls._service_date(observed_at, scheduled)}|{station}|{train}|{scheduled}"

    @staticmethod
    def _signature(real: str, delay: int) -> str:
        return f"{real}|{delay}"

    def _load_seen(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            try:
                observed = datetime.fromisoformat(item["observed_at"])
                key = self._key(
                    observed,
                    str(item.get("station") or ""),
                    str(item.get("train") or ""),
                    str(item.get("scheduled") or ""),
                )
                self._seen[key] = self._signature(
                    str(item.get("real") or ""),
                    int(item.get("delay_minutes") or 0),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def record(
        self,
        station: str,
        departures: list[dict[str, Any]],
        *,
        observed_at: datetime | None = None,
    ) -> int:
        """Append changed observations; return the number of new lines."""
        if not isinstance(departures, list) or not departures:
            return 0
        moment = observed_at or self._current()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        station_code = str(station or "").strip()
        lines: list[str] = []
        with self._lock:
            for departure in departures:
                if not isinstance(departure, dict):
                    continue
                train = str(departure.get("train") or "").strip()
                scheduled = str(departure.get("scheduled") or "").strip()
                real = str(departure.get("real") or scheduled).strip()
                if not (train and scheduled and real):
                    continue
                try:
                    delay = int(departure.get("delay_minutes") or 0)
                    level = int(departure.get("delay_level") or 0)
                except (TypeError, ValueError):
                    delay, level = 0, 0
                key = self._key(moment, station_code, train, scheduled)
                service_date = key.split("|", 1)[0]
                signature = self._signature(real, delay)
                if self._seen.get(key) == signature:
                    continue
                self._seen[key] = signature
                lines.append(
                    json.dumps(
                        {
                            "observed_at": moment.isoformat(),
                            "date": service_date,
                            "station": station_code,
                            "train": train,
                            "scheduled": scheduled,
                            "real": real,
                            "delay_minutes": delay,
                            "delay_level": level,
                            "destination_code": departure.get("destination_code"),
                            "product": departure.get("product"),
                        },
                        ensure_ascii=False,
                    )
                )
            if lines:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
        return len(lines)

    def observations(self, *, days: int = 7, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = now or self._current()
        cutoff = moment - timedelta(days=max(1, min(365, int(days))))
        out: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                item = json.loads(line)
                observed = datetime.fromisoformat(item["observed_at"])
            except (ValueError, KeyError, TypeError):
                continue
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            if observed < cutoff:
                continue
            out.append(item)
        out.sort(key=lambda item: str(item.get("observed_at") or ""))
        return out

    def summary(self, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
        rows = self.observations(days=days, now=now)
        latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in rows:
            key = (
                str(item.get("date") or ""),
                str(item.get("station") or ""),
                str(item.get("train") or ""),
                str(item.get("scheduled") or ""),
            )
            latest[key] = item

        delays: list[int] = []
        valid_items: list[dict[str, Any]] = []
        for item in latest.values():
            try:
                delays.append(max(0, int(item.get("delay_minutes") or 0)))
            except (TypeError, ValueError):
                continue
            valid_items.append(item)
        count = len(delays)
        if not count:
            return {
                "sample_size": 0,
                "on_time": 0,
                "delayed": 0,
                "severe": 0,
                "on_time_pct": None,
                "average_delay": None,
                "median_delay": None,
                "max_delay": None,
                "stations": 0,
                "trains": 0,
                "from": None,
                "to": None,
                "note": "No local live-board observations yet.",
            }

        on_time = sum(1 for value in delays if value <= ON_TIME_LIMIT)
        severe = sum(1 for value in delays if value > SEVERE_LIMIT)
        delayed = count - on_time
        stations = {str(item.get("station") or "") for item in valid_items}
        trains = {str(item.get("train") or "") for item in valid_items}
        dates = sorted(str(item.get("date") or "") for item in valid_items)
        return {
            "sample_size": count,
            "on_time": on_time,
            "delayed": delayed,
            "severe": severe,
            "on_time_pct": round(on_time * 100 / count, 1),
            "average_delay": round(sum(delays) / count, 1),
            "median_delay": statistics.median(delays),
            "max_delay": max(delays),
            "stations": len(stations),
            "trains": len(trains),
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "note": (
                "Local observations from live station boards; "
                "not an official ONCF punctuality dataset."
            ),
        }
