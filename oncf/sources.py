"""Health of the API gateway used by the tracker.

All runtime ONCF traffic goes through the configured gateway. This module only
checks that gateway with plain DNS/HTTP probes; it does not call any upstream
host directly.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from .config import ONCF_BASE_URL
from .http import USER_AGENT, _doh_lookup, _forced_address, ensure_os_trust

GATEWAY = urlparse(ONCF_BASE_URL)
GATEWAY_HOST = GATEWAY.hostname or ""
GATEWAY_PATH = GATEWAY.path.rstrip("/")


def _dns(host: str) -> tuple[str, str | None]:
    ip = _doh_lookup(host)
    return ("ok" if ip else "NXDOMAIN", ip)


def _http_json(
    host: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int | None, Any | None]:
    """Small JSON probe with the same DoH/TLS handling as the app client."""
    ip = _doh_lookup(host)
    if not ip:
        return None, None
    ensure_os_trust()
    try:
        with _forced_address(host, ip):
            response = requests.request(
                method,
                f"https://{host}{path}",
                timeout=20,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                json=body,
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload
    except requests.RequestException:
        return None, None


def probe_sources() -> dict[str, Any]:
    """Check the gateway endpoints without touching the upstream hosts."""
    checks: list[dict[str, Any]] = []
    dns, ip = _dns(GATEWAY_HOST)

    gateway_http, _ = (
        _http_json(GATEWAY_HOST, f"{GATEWAY_PATH}/cache/getServerTimeZone") if ip else (None, None)
    )
    checks.append(
        {
            "name": "API gateway — timetable and stations",
            "host": GATEWAY_HOST,
            "purpose": "Station catalogue and official timetables",
            "dns": dns,
            "http": gateway_http,
            "ok": dns == "ok" and gateway_http == 200,
            "note": "" if gateway_http == 200 else "No JSON answer from the gateway",
        }
    )

    live_http, live_payload = (
        _http_json(
            GATEWAY_HOST,
            f"{GATEWAY_PATH}/mobile/DepartingTrains",
            method="POST",
            body={"codeGare": "231"},
        )
        if ip
        else (None, None)
    )
    live_head = (live_payload or {}).get("head") or {}
    live_ok = live_http == 200 and str(live_head.get("errorCode")) == "0"
    checks.append(
        {
            "name": "API gateway — live station departures",
            "host": GATEWAY_HOST,
            "purpose": "Real departure times and delay levels",
            "dns": dns,
            "http": live_http,
            "ok": live_ok,
            "note": (
                "Live board answered; this is a station board, not a train position."
                if live_ok
                else "No successful live board answer"
            ),
        }
    )

    news_http, news_payload = (
        _http_json(GATEWAY_HOST, f"{GATEWAY_PATH}/media/CMS/en/news") if ip else (None, None)
    )
    news = (news_payload or {}).get("news") if isinstance(news_payload, dict) else None
    news_ok = news_http == 200 and isinstance(news, list) and bool(news)
    checks.append(
        {
            "name": "API gateway — traffic notices",
            "host": GATEWAY_HOST,
            "purpose": "Official works / service notices",
            "dns": dns,
            "http": news_http,
            "ok": news_ok,
            "note": f"{len(news)} current notice(s)" if news_ok else "No successful news answer",
        }
    )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "note": (
            "All data is requested through the private API gateway. "
            "Live station departure boards are available; train positions and platform "
            "assignments are not."
        ),
    }


class SourceStatusService:
    """Cache source probes so opening the panel does not hammer the gateway."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = max(30.0, ttl_seconds)
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._checked_at: datetime | None = None

    def get(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            age = (now - self._checked_at).total_seconds() if self._checked_at else None
            if self._payload is not None and not refresh and age is not None and age < self.ttl_seconds:
                return self._payload
            if self._payload is not None and refresh and age is not None and age < 30:
                return self._payload
            try:
                self._payload = probe_sources()
                self._checked_at = now
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                self._payload = {
                    "checked_at": now.isoformat(),
                    "checks": [],
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "note": "Could not complete the source check.",
                }
            return self._payload
