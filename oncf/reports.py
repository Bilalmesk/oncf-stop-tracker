"""Local community reports for departures, cancellations and delays.

This is deliberately a small local JSON store, not a shared social network. It
lets the person using the app record what they see at the station and show it
next to the timetable. Reports are **unverified community observations**, never
official ONCF data.

The API is intentionally replaceable: point ``ReportStore`` at a database or a
remote service later without changing the HTTP/UI contract.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR

UTC = timezone.utc
KINDS = {"cancellation", "departure_delay", "delay", "other"}
DEFAULT_TTL = {
    "cancellation": timedelta(hours=12),
    "departure_delay": timedelta(hours=3),
    "other": timedelta(hours=6),
}
MAX_MESSAGE = 280
MAX_TRAIN = 24
MAX_STATION = 24
MAX_REPORTS = 2000
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ReportRateLimited(RuntimeError):
    """Raised when one client submits reports too quickly."""


class ReportStore:
    """Thread-safe, atomically-written JSON report store."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path or (DATA_DIR / "reports" / "reports.json"))
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._lock = threading.Lock()

    # --- storage --------------------------------------------------------
    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        reports = raw.get("reports") if isinstance(raw, dict) else raw
        if not isinstance(reports, list):
            return []
        return [item for item in reports if isinstance(item, dict)]

    def _save(self, reports: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "reports": reports[-MAX_REPORTS:]}
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def _current(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _parse_expiry(report: dict[str, Any]) -> datetime | None:
        try:
            value = datetime.fromisoformat(report.get("expires_at", ""))
        except (TypeError, ValueError):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _public(
        self,
        report: dict[str, Any],
        now: datetime,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        expiry = self._parse_expiry(report)
        active = expiry is not None and expiry > now
        try:
            created = datetime.fromisoformat(report.get("created_at", ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age = max(0, int((now - created).total_seconds() // 60))
        except (TypeError, ValueError):
            age = None
        expires_in = (
            max(0, int((expiry - now).total_seconds() // 60)) if expiry else None
        )
        votes = report.get("votes") if isinstance(report.get("votes"), dict) else {}
        payload = {key: value for key, value in report.items() if key != "votes"}
        return {
            **payload,
            "confirmations": self._count(report.get("confirmations")),
            "disputes": self._count(report.get("disputes")),
            "active": active,
            "age_minutes": age,
            "expires_in_minutes": expires_in,
            "my_vote": votes.get(client_id) if client_id else None,
        }

    @staticmethod
    def _clean_client(client_id: str | None) -> str:
        text = str(client_id or "").strip()
        if not CLIENT_ID_RE.fullmatch(text):
            raise ValueError("client_id must be 8-64 letters, digits, '-' or '_'")
        return text

    # --- validation -----------------------------------------------------
    @staticmethod
    def _clean_text(value: Any, *, field: str, limit: int, required: bool = False) -> str:
        text = str(value or "").strip()
        if required and not text:
            raise ValueError(f"{field} is required")
        if len(text) > limit:
            raise ValueError(f"{field} must be at most {limit} characters")
        return text

    def _validate(
        self,
        kind: str,
        train: str,
        station: str | None,
        date: str | None,
        minutes: int | None,
        message: str,
    ) -> dict[str, Any]:
        clean_kind = str(kind or "").strip().lower()
        if clean_kind == "delay":
            clean_kind = "departure_delay"
        if clean_kind not in KINDS:
            raise ValueError("unknown report kind")
        clean_train = self._clean_text(train, field="train", limit=MAX_TRAIN, required=True).upper()
        clean_station = self._clean_text(
            station, field="station", limit=MAX_STATION
        ).upper() or None
        clean_date = self._clean_text(date, field="date", limit=10)
        if clean_date:
            try:
                parsed_date = date_type.fromisoformat(clean_date)
            except ValueError as exc:
                raise ValueError("date must be YYYY-MM-DD") from exc
            if parsed_date.isoformat() != clean_date:
                raise ValueError("date must be YYYY-MM-DD")
            clean_date = parsed_date.isoformat()
        clean_message = self._clean_text(message, field="message", limit=MAX_MESSAGE)

        clean_minutes: int | None = None
        if clean_kind == "departure_delay":
            if isinstance(minutes, float) and not minutes.is_integer():
                raise ValueError("minutes must be an integer")
            try:
                clean_minutes = int(minutes or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("minutes must be an integer") from exc
            if not 1 <= clean_minutes <= 600:
                raise ValueError("minutes must be between 1 and 600")
        return {
            "kind": clean_kind,
            "train": clean_train,
            "station": clean_station,
            "date": clean_date or None,
            "minutes": clean_minutes,
            "message": clean_message,
        }

    # --- public API -----------------------------------------------------
    def add(
        self,
        *,
        kind: str,
        train: str,
        station: str | None = None,
        date: str | None = None,
        minutes: int | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        clean = self._validate(kind, train, station, date, minutes, message)
        now = self._current()
        ttl = DEFAULT_TTL[clean["kind"]]
        report = {
            "id": uuid.uuid4().hex[:12],
            **clean,
            "created_at": now.isoformat(),
            "expires_at": (now + ttl).isoformat(),
            "confirmations": 0,
            "disputes": 0,
            "votes": {},
        }
        with self._lock:
            reports = self._load()
            reports.append(report)
            self._save(reports)
        return self._public(report, now)

    def list(
        self,
        *,
        train: str | None = None,
        station: str | None = None,
        date: str | None = None,
        active_only: bool = True,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now = self._current()
        client = self._clean_client(client_id) if client_id else None
        train_key = str(train or "").strip().upper()
        station_key = str(station or "").strip().upper()
        date_key = None
        if date:
            raw_date = str(date).strip()
            try:
                parsed_date = date_type.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValueError("date must be YYYY-MM-DD") from exc
            if parsed_date.isoformat() != raw_date:
                raise ValueError("date must be YYYY-MM-DD")
            date_key = parsed_date.isoformat()
        with self._lock:
            reports = self._load()
        out: list[dict[str, Any]] = []
        for report in reports:
            if train_key and str(report.get("train") or "").upper() != train_key:
                continue
            if station_key and str(report.get("station") or "").upper() != station_key:
                continue
            if date_key and report.get("date") != date_key:
                continue
            item = self._public(report, now, client)
            if active_only and not item["active"]:
                continue
            out.append(item)
        out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return out

    def vote(
        self,
        report_id: str,
        vote: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        choice = str(vote or "").strip().lower()
        if choice not in {"confirm", "dispute"}:
            raise ValueError("vote must be 'confirm' or 'dispute'")
        client = self._clean_client(client_id)
        now = self._current()
        with self._lock:
            reports = self._load()
            for report in reports:
                if report.get("id") != report_id:
                    continue
                votes = report.setdefault("votes", {})
                if not isinstance(votes, dict):
                    votes = {}
                    report["votes"] = votes
                previous = votes.get(client)
                if previous == choice:
                    return self._public(report, now, client)
                if previous == "confirm":
                    report["confirmations"] = max(0, self._count(report.get("confirmations")) - 1)
                elif previous == "dispute":
                    report["disputes"] = max(0, self._count(report.get("disputes")) - 1)
                key = "confirmations" if choice == "confirm" else "disputes"
                report[key] = self._count(report.get(key)) + 1
                votes[client] = choice
                self._save(reports)
                return self._public(report, now, client)
        raise KeyError(report_id)

    def active_for_trip(self, train: str, date: str | None) -> list[dict[str, Any]]:
        return self.list(train=train, date=date)

    def delay_for_trip(self, train: str, date: str | None) -> tuple[int, str]:
        """Return the strongest community delay for a trip and its source."""
        reports = [
            item
            for item in self.active_for_trip(train, date)
            if item.get("kind") == "departure_delay"
        ]
        minutes = max((int(item.get("minutes") or 0) for item in reports), default=0)
        return (minutes, "community" if minutes else "none")
