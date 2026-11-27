from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncf.traffic import MOBILE_SOURCE_NAME, TrafficService, extract_notices

ROOT = Path(__file__).resolve().parent.parent


PAYLOAD = {
    "pages": [
        {
            "pageName": "Black Week",
            "pageSlug": "black-friday",
            "pageBlocks": [
                {"blockName": "paragraphe", "props": {"content": "<p>Sale</p>"}}
            ],
        },
        {
            "pageName": "ATS Sidi Maarouf station",
            "pageSlug": "info-travaux",
            "pageBlocks": [
                None,
                {
                    "blockName": "imagecontenu",
                    "props": {"content": "<h2>TRACK WORKS</h2><h2>CASA &ndash; SIDI MAAROUF</h2>"},
                },
                {
                    "blockName": "paragraphe",
                    "props": {
                        "content": (
                            "<p>From <strong>28 Nov</strong> to 30 Nov.</p>"
                            "<ul><li>Line A suspended</li><li>Line B diverted</li></ul>"
                        )
                    },
                },
                {"blockName": "titre", "props": {"content": "More info"}},
                {"blockName": "paragraphe", "props": {"content": "<p>Call 2255.</p>"}},
            ],
        },
        {
            "pageName": "New plan",
            "pageSlug": "info-travaux-2",
            "pageBlocks": [
                {"blockName": "imagecontenu", "props": {"content": "<h2>New plan</h2>"}},
                {"blockName": "paragraphe", "props": {"content": "<p>Starting 14 Sep.</p>"}},
            ],
        },
        {
            "pageName": "Image only",
            "pageSlug": "avis-travaux-x",
            "pageBlocks": [
                {"blockName": "miseenavant", "props": {"content": "Call 2255"}}
            ],
        },
    ]
}


def test_extract_notices_orders_and_cleans_html():
    notices = extract_notices(PAYLOAD)
    assert [n["slug"] for n in notices] == ["info-travaux", "info-travaux-2"]

    first = notices[0]
    assert first["title"] == "TRACK WORKS — CASA – SIDI MAAROUF"
    assert "<" not in first["text"] and "&" not in first["text"]
    assert "• Line A suspended" in first["text"]
    assert "• Line B diverted" in first["text"]
    assert "More info" in first["text"]
    assert first["url"].endswith("/info-travaux")
    assert first["summary"].startswith("From 28 Nov to 30 Nov.")


def test_extract_notices_skips_pages_without_text():
    payload = {
        "pages": [
            {
                "pageName": "Image notice",
                "pageSlug": "info-travaux-old",
                "pageBlocks": [
                    {"blockName": "imagecontenu", "props": {"image": "x.jpg"}},
                    {"blockName": "espaceur", "props": {}},
                ],
            }
        ]
    }
    assert extract_notices(payload) == []
    assert extract_notices({}) == []
    assert extract_notices({"pages": "nope"}) == []


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def request_json(self, method, path, **kwargs):
        self.calls.append((method, path))
        if self.error:
            raise self.error
        return self.payload


def test_service_caches_and_refreshes(tmp_path):
    client = FakeClient(PAYLOAD)
    service = TrafficService(client, cache_path=tmp_path / "traffic-en.json", ttl_seconds=3600)

    first = service.get()
    assert first["error"] is None
    assert first["stale"] is False
    assert len(first["notices"]) == 2
    assert client.calls == [("GET", "/media/CMS/en/pages")]

    service.get()
    assert len(client.calls) == 1

    # A manual refresh older than a minute fetches again.
    service._fetched_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    service.get(refresh=True)
    assert len(client.calls) == 2


def test_service_falls_back_to_disk_cache_on_failure(tmp_path):
    cache = tmp_path / "traffic-en.json"
    warm = TrafficService(FakeClient(PAYLOAD), cache_path=cache)
    assert warm.get()["notices"]

    cold = TrafficService(FakeClient(error=RuntimeError("network down")), cache_path=cache)
    cold._fetched_at = datetime.now(timezone.utc) - timedelta(hours=2)
    result = cold.get(refresh=True)
    assert result["stale"] is True
    assert "network down" in (result["error"] or "")
    assert [n["slug"] for n in result["notices"]] == ["info-travaux", "info-travaux-2"]


def test_service_reports_error_without_cache(tmp_path):
    service = TrafficService(
        FakeClient(error=RuntimeError("boom")),
        cache_path=tmp_path / "missing.json",
    )
    result = service.get()
    assert result["notices"] == []
    assert "boom" in (result["error"] or "")
    assert result["source_url"].endswith("/info-travaux")

def test_service_prefers_mobile_news(tmp_path):
    news_path = ROOT / "data" / "official" / "mobile_news_fr.json"
    if not news_path.exists():
        pytest.skip("official mobile news fixture is not available")
    news_payload = json.loads(news_path.read_text(encoding="utf-8"))
    news_client = FakeClient(news_payload)
    pages_client = FakeClient(error=RuntimeError("pages should not be called"))
    service = TrafficService(
        pages_client,
        news_client=news_client,
        cache_path=tmp_path / "traffic-en.json",
    )
    result = service.get()
    assert result["source"] == MOBILE_SOURCE_NAME
    assert result["error"] is None
    assert len(result["notices"]) >= 4
    assert news_client.calls == [("GET", "/media/CMS/en/news")]
    assert pages_client.calls == []


def test_service_falls_back_to_pages_when_news_fails(tmp_path):
    news_client = FakeClient(error=RuntimeError("news down"))
    pages_client = FakeClient(PAYLOAD)
    service = TrafficService(
        pages_client,
        news_client=news_client,
        cache_path=tmp_path / "traffic-en.json",
    )
    result = service.get()
    assert result["source"].startswith("ONCF traffic notices")
    assert result["error"] is None
    assert len(result["notices"]) == 2
    assert news_client.calls == [("GET", "/media/CMS/en/news")]
    assert pages_client.calls == [("GET", "/media/CMS/en/pages")]
