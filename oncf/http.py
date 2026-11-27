"""HTTP plumbing: polite throttling, retries, and a DNS fallback.

The fallback exists because some networks fail to resolve the gateway hostname
even though public DNS knows it. Rather than hard-coding an IP permanently, the
resolver is asked over DNS-over-HTTPS and the answer is used for that one
connection while the hostname (and therefore TLS SNI) stays intact.

Set ``ONCF_DNS_OVERRIDE=1.2.3.4`` to force a specific address, or
``ONCF_DISABLE_DOH=1`` to turn the fallback off.
"""
from __future__ import annotations

import contextlib
import os
import posixpath
import socket
import threading
import time
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

DOH_URL = "https://dns.google/resolve"

USER_AGENT = (
    "ONCF-Stop-Tracker/0.1 (open-source research prototype; "
    "contact: oncf-tracker@example.invalid)"
)

_TRUSTSTORE_READY = False


def ensure_os_trust() -> bool:
    """Use the operating system certificate store when available.

    ONCF's server does not always send the full chain, and the ``certifi``
    bundle shipped with Python can then fail with "unable to get local issuer
    certificate" while browsers and curl succeed. ``truststore`` fixes that
    without disabling verification.
    """
    global _TRUSTSTORE_READY
    if _TRUSTSTORE_READY:
        return True
    if os.environ.get("ONCF_DISABLE_OS_TRUST"):
        return False
    try:
        import truststore

        truststore.inject_into_ssl()
        _TRUSTSTORE_READY = True
    except Exception:
        _TRUSTSTORE_READY = False
    return _TRUSTSTORE_READY


class OncfApiError(RuntimeError):
    """Raised when the source returns an error body or an unusable response."""


def is_gateway_resource_url(base_url: str, url: str) -> bool:
    """True only for HTTPS URLs on the configured gateway and under its path.

    CMS responses are sanitized by the gateway, but this is the last line of
    defence: even a malformed or future upstream link cannot make the public
    client fetch an ONCF host directly.
    """
    base = urlparse(base_url)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not base.hostname:
        return False
    if parsed.hostname != base.hostname:
        return False
    base_path = posixpath.normpath(base.path.rstrip("/") or "/")
    path = posixpath.normpath(parsed.path or "/")
    if base_path == "/":
        return True
    return path == base_path or path.startswith(base_path + "/")


def _doh_lookup(host: str, timeout: float = 10.0) -> str | None:
    try:
        r = requests.get(
            DOH_URL,
            params={"name": host, "type": "A"},
            timeout=timeout,
            headers={"Accept": "application/dns-json", "User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        for answer in r.json().get("Answer", []):
            if answer.get("type") == 1 and answer.get("data"):
                return answer["data"]
    except Exception:
        return None
    return None


@contextlib.contextmanager
def _forced_address(host: str, ip: str) -> Iterator[None]:
    original = socket.getaddrinfo

    def patched(hostname, port, family=0, type=0, proto=0, flags=0):
        if hostname == host:
            return original(ip, port, family, type, proto, flags)
        return original(hostname, port, family, type, proto, flags)

    socket.getaddrinfo = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


class HttpClient:
    """Small JSON client with a minimum gap between requests."""

    def __init__(self, base_url: str, *, min_interval: float = 1.0, timeout: float = 30.0):
        ensure_os_trust()
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
            }
        )
        self.request_count = 0

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            self.request_count += 1

    def request_bytes(self, method: str, path_or_url: str, **kwargs: Any) -> tuple[bytes, Any]:
        """Fetch a non-JSON resource (e.g. an official PDF) with the same retry rules."""
        url = (
            path_or_url
            if path_or_url.lower().startswith(("http://", "https://"))
            else f"{self.base_url}{path_or_url}"
        )
        if not is_gateway_resource_url(self.base_url, url):
            raise OncfApiError("refusing non-gateway resource URL")
        host = requests.utils.urlparse(url).hostname or ""
        override = os.environ.get("ONCF_DNS_OVERRIDE")
        headers = {"Accept": "application/pdf, application/octet-stream, */*"}
        headers.update(kwargs.pop("headers", {}) or {})

        last_error: Exception | None = None
        for attempt in range(3):
            self._throttle()
            try:
                with self._maybe_force(host, override, attempt):
                    response = self._session.request(
                        method, url, timeout=self.timeout, headers=headers, **kwargs
                    )
                if response.status_code >= 500:
                    raise OncfApiError(f"HTTP {response.status_code} from {url}")
                response.raise_for_status()
                return response.content, response.headers
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
            except requests.HTTPError as exc:
                raise OncfApiError(f"HTTP error for {url}: {exc}") from exc
        raise OncfApiError(f"could not reach {url}") from last_error

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        host = requests.utils.urlparse(url).hostname or ""
        override = os.environ.get("ONCF_DNS_OVERRIDE")

        last_error: Exception | None = None
        for attempt in range(3):
            self._throttle()
            try:
                with self._maybe_force(host, override, attempt):
                    response = self._session.request(method, url, timeout=self.timeout, **kwargs)
                if response.status_code >= 500:
                    raise OncfApiError(f"HTTP {response.status_code} from {path}")
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
            except requests.HTTPError as exc:
                raise OncfApiError(f"HTTP error for {path}: {exc}") from exc
        raise OncfApiError(f"could not reach {url}") from last_error

    @contextlib.contextmanager
    def _maybe_force(self, host: str, override: str | None, attempt: int) -> Iterator[None]:
        if os.environ.get("ONCF_DISABLE_DOH") or not host:
            yield
            return
        ip = override or (_doh_lookup(host) if attempt > 0 else None)
        if not ip:
            yield
            return
        with _forced_address(host, ip):
            yield
