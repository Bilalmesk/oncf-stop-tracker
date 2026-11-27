"""Official ONCF works / traffic notices through the API gateway.

The mobile API exposes a compact ``/media/CMS/{lang}/news`` feed. The tracker
prefers it and falls back to the web ``/media/CMS/{lang}/pages`` dump. Both are
public, need no credentials and are extracted to plain text.

These are **service notices**, not live train positions or guaranteed delay
data. The page is fetched at most once per ``ttl`` and cached on disk under the
ignored ``data/cache`` directory.
"""
from __future__ import annotations

import html as html_module
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ONCF_BASE_URL, ONCF_MOBILE_BASE

SOURCE_NAME = "ONCF traffic notices — gateway"
SOURCE_URL = f"{ONCF_BASE_URL}/info-travaux"
NOTICE_NOTE = "Official works/service notice. Not live train positions."
DEFAULT_TTL = 1800.0

MOBILE_SOURCE_NAME = "ONCF traffic notices — gateway news"
MOBILE_SOURCE_URL = f"{ONCF_MOBILE_BASE}/media/CMS/{{lang}}/news"

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(value: str | None) -> str:
    """Turn the CMS block HTML into readable text, keeping bullets and breaks."""
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)<li[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)</(?:p|div|h[1-6]|li|tr)>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html_module.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1] != ""):
            out.append(line)
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(out)).strip()


def _page_sections(page: dict[str, Any]) -> tuple[str | None, list[dict[str, str]]]:
    """Return (first heading, sections) for one CMS page."""
    title: str | None = None
    sections: list[dict[str, str]] = []
    for block in page.get("pageBlocks") or []:
        if not isinstance(block, dict):
            continue
        name = block.get("blockName")
        props = block.get("props") or {}
        if name in {"titre", "imagecontenu"}:
            text = _html_to_text(props.get("content") or props.get("title"))
            if not text:
                continue
            if title is None:
                title = " — ".join(line.strip() for line in text.splitlines() if line.strip())
            sections.append({"heading": text, "text": ""})
        elif name in {"paragraphe", "miseenavant"}:
            text = _html_to_text(props.get("content"))
            if text:
                sections.append({"heading": "", "text": text})
    # Merge consecutive paragraphs under their heading, dropping empty sections.
    merged: list[dict[str, str]] = []
    for section in sections:
        if section["heading"]:
            merged.append({"heading": section["heading"], "text": section["text"]})
        elif merged and not merged[-1]["text"]:
            merged[-1]["text"] = section["text"]
        else:
            merged.append({"heading": "", "text": section["text"]})
    return title, [section for section in merged if section["text"].strip()]


def _notice_url(slug: str) -> str:
    return SOURCE_URL


def extract_notices(payload: dict[str, Any], lang: str = "en") -> list[dict[str, Any]]:
    """Extract the works notices from a CMS ``pages`` response."""
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list):
        return []

    notices: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        slug = str(page.get("pageSlug") or "").strip()
        if not slug.startswith("info-travaux"):
            continue
        title, sections = _page_sections(page)
        if not sections:
            continue
        text = "\n\n".join(
            (section["heading"] + "\n" + section["text"]).strip()
            if section["heading"] else section["text"]
            for section in sections
        )
        first_body = next((s["text"] for s in sections if s["text"]), "")
        summary = re.sub(r"\s+", " ", first_body).strip()
        notices.append(
            {
                "slug": slug,
                "title": (title or page.get("pageName") or slug)[:160],
                "page_name": page.get("pageName") or "",
                "summary": summary,
                "text": text,
                "url": _notice_url(slug),
            }
        )

    order = {"info-travaux": 0, "info-travaux-2": 1}
    notices.sort(key=lambda item: (order.get(item["slug"], 2), item["slug"]))
    return notices


def extract_news(payload: dict[str, Any], lang: str = "fr") -> list[dict[str, Any]]:
    """Normalize the compact mobile ``/media/CMS/<lang>/news`` feed.

    The mobile feed returns one ``news`` array with a title, dates and an HTML
    body per item. It is the same content as the public works page, but about
    4 KB instead of the ~1 MB ``pages`` dump.
    """
    items = payload.get("news") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    notices: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        title = _html_to_text(item.get("title")) or f"ONCF notice {index}"
        text = _html_to_text(item.get("content"))
        if not text:
            continue
        summary = re.sub(r"\s+", " ", text).strip()
        notices.append(
            {
                "slug": f"news-{index}",
                "title": title[:160],
                "page_name": title,
                "summary": summary[:320],
                "text": text,
                "url": SOURCE_URL,
                "start_at": item.get("startAT"),
                "end_at": item.get("endAt"),
            }
        )
    return notices


class TrafficService:
    """Fetch, cache and serve ONCF works notices."""

    def __init__(
        self,
        client: Any,
        *,
        lang: str = "en",
        ttl_seconds: float = DEFAULT_TTL,
        cache_path: Path | None = None,
        news_client: Any | None = None,
    ) -> None:
        self.client = client
        self.news_client = news_client
        self.lang = lang if lang in {"en", "fr", "ar"} else "en"
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self.cache_path = cache_path or (DATA_DIR / "cache" / f"traffic-{self.lang}.json")
        self._lock = threading.Lock()
        self._notices: list[dict[str, Any]] = []
        self._fetched_at: datetime | None = None
        self._error: str | None = None
        self._source_name = SOURCE_NAME
        self._source_url = SOURCE_URL
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            notices = data.get("notices")
            fetched_at = data.get("fetched_at")
            if isinstance(notices, list) and fetched_at:
                self._notices = notices
                self._fetched_at = datetime.fromisoformat(fetched_at)
                source = data.get("source")
                source_url = data.get("source_url")
                if source:
                    self._source_name = source
                if source_url:
                    self._source_url = source_url
        except (OSError, ValueError, TypeError):
            return

    def _save_disk_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(
                    {
                        "fetched_at": self._fetched_at.isoformat() if self._fetched_at else None,
                        "source": self._source_name,
                        "source_url": self._source_url,
                        "notices": self._notices,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            return

    def _payload(self, *, stale: bool) -> dict[str, Any]:
        return {
            "source": self._source_name,
            "source_url": self._source_url,
            "language": self.lang,
            "fetched_at": self._fetched_at.isoformat() if self._fetched_at else None,
            "stale": stale,
            "notices": self._notices,
            "error": self._error,
            "note": NOTICE_NOTE,
        }

    def _fetch(self) -> list[dict[str, Any]]:
        """Prefer the compact mobile news feed, fall back to the CMS pages dump."""
        errors: list[str] = []
        if self.news_client is not None:
            try:
                payload = self.news_client.request_json(
                    "GET", f"/media/CMS/{self.lang}/news"
                )
                notices = extract_news(payload, self.lang)
                if notices:
                    self._source_name = MOBILE_SOURCE_NAME
                    self._source_url = MOBILE_SOURCE_URL.format(lang=self.lang)
                    return notices
                errors.append("mobile news returned no notices")
            except Exception as exc:  # noqa: BLE001 - fall back to the pages API
                errors.append(f"mobile news: {type(exc).__name__}: {exc}")

        try:
            payload = self.client.request_json("GET", f"/media/CMS/{self.lang}/pages")
            notices = extract_notices(payload, self.lang)
            if not notices:
                raise ValueError("ONCF CMS response contained no works notices")
        except Exception as exc:
            if errors:
                raise ValueError(f"{exc}; {'; '.join(errors)}") from exc
            raise
        self._source_name = SOURCE_NAME
        self._source_url = SOURCE_URL
        return notices

    def get(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            age = (now - self._fetched_at).total_seconds() if self._fetched_at else None
            fresh = age is not None and age < self.ttl_seconds
            # A manual refresh is honoured, but never more than once a minute.
            if fresh and (not refresh or age is not None and age < 60):
                return self._payload(stale=self._error is not None)

            try:
                notices = self._fetch()
                self._notices = notices
                self._fetched_at = now
                self._error = None
                self._save_disk_cache()
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not raised
                self._error = f"{type(exc).__name__}: {exc}"[:300]

            return self._payload(stale=self._error is not None)
