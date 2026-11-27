from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oncf.mobile import (
    LiveDeparturesService,
    MobileOncfClient,
    delay_minutes,
    parse_departures,
)
from oncf.stations import StationCatalog
from oncf.traffic import extract_news

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "data" / "official"


def load(name: str):
    path = OFFICIAL / name
    if not path.exists():
        pytest.skip(f"official fixture {name} is not available")
    return json.loads(path.read_text(encoding="utf-8"))


def catalog() -> StationCatalog:
    path = OFFICIAL / "cache_stations.json"
    if not path.exists():
        pytest.skip("official station cache is not available")
    return StationCatalog.from_file(path)


class FakeMobileClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls: list[str] = []

    def departing_trains(self, code):
        self.calls.append(str(code))
        if self.error:
            raise self.error
        return self.payload


def test_parse_departures_enriches_live_rows():
    stations = catalog()
    departures = parse_departures(load("mobile_departing_231.json"), stations)

    assert len(departures) == 4
    train_206 = next(d for d in departures if d.train == "206")
    assert train_206.scheduled == "22:36:00"
    assert train_206.real == "22:49:48"
    assert train_206.delay_minutes == 14
    assert train_206.delay_level == 2
    assert train_206.delay_label == "medium"
    assert train_206.is_delayed is True
    assert train_206.destination_code == "200"
    assert train_206.destination_name == "CASA VOYAGEURS"
    assert train_206.product == "Al Atlas"
    assert train_206.product_color == "#F87820"

    on_time = next(d for d in departures if d.train == "59")
    assert on_time.delay_minutes == 0
    assert on_time.delay_label == "on_time"
    assert on_time.is_delayed is False
    assert on_time.product == "TNR"


def test_delay_handles_midnight_rollover():
    departures = parse_departures(load("mobile_departing_363.json"), catalog())
    train_307 = next(d for d in departures if d.train == "307")
    assert train_307.scheduled == "23:22:00"
    assert train_307.real == "00:05:07"
    assert train_307.delay_minutes == 43
    assert train_307.delay_label == "high"
    assert delay_minutes("23:22:00", "00:05:07") == 43
    assert delay_minutes("22:36:00", "22:49:48") == 14


def test_live_service_caches_and_refreshes(tmp_path):
    payload = load("mobile_departing_231.json")
    client = FakeMobileClient(payload)
    service = LiveDeparturesService(client, catalog(), ttl_seconds=60, cache_dir=tmp_path)

    first = service.get("231")
    assert len(first["departures"]) == 4
    assert first["error"] is None
    assert first["stale"] is False
    assert client.calls == ["231"]
    assert first["station"]["code"] == "231"
    assert first["station"]["name"] == "RABAT VILLE"
    assert first["source"].startswith("ONCF live station board")

    service.get("231")
    assert client.calls == ["231"]

    # Age the in-memory entry beyond the refresh throttle, then force a fetch.
    from oncf.mobile import parse_departures

    old = datetime.now(timezone.utc) - timedelta(seconds=61)
    service._memory["231"] = (old, parse_departures(payload, catalog()))
    service.get("231", refresh=True)
    assert client.calls == ["231", "231"]


def test_live_service_falls_back_to_disk_cache(tmp_path):
    payload = load("mobile_departing_231.json")
    warm = LiveDeparturesService(FakeMobileClient(payload), catalog(), cache_dir=tmp_path)
    warm.get("231")

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    warm._write_disk("231", old, warm._memory["231"][1], None)

    cold = LiveDeparturesService(FakeMobileClient(error=RuntimeError("network down")), catalog(), cache_dir=tmp_path)
    result = cold.get("231", refresh=True)
    assert len(result["departures"]) == 4
    assert result["stale"] is True
    assert "network down" in (result["error"] or "")


def test_live_service_offline_reads_disk(tmp_path):
    payload = load("mobile_departing_231.json")
    warm = LiveDeparturesService(FakeMobileClient(payload), catalog(), cache_dir=tmp_path)
    warm.get("231")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    warm._write_disk("231", old, warm._memory["231"][1], None)

    offline = LiveDeparturesService(None, catalog(), cache_dir=tmp_path)
    result = offline.get("231", refresh=True)
    assert len(result["departures"]) == 4
    assert result["stale"] is True
    assert "offline" in (result["error"] or "")


def test_live_service_serves_fresh_disk_without_network(tmp_path):
    payload = load("mobile_departing_231.json")
    warm = LiveDeparturesService(FakeMobileClient(payload), catalog(), cache_dir=tmp_path)
    warm.get("231")

    offline = LiveDeparturesService(None, catalog(), cache_dir=tmp_path)
    result = offline.get("231")
    assert len(result["departures"]) == 4
    assert result["error"] is None
    assert result["stale"] is False


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request_json(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response


def test_mobile_client_paths_and_schedule_date():
    client = MobileOncfClient()
    http = FakeHttp({"head": {"errorCode": 0}, "body": {}})
    client.http = http

    client.departing_trains("231")
    method, path, kwargs = http.calls[-1]
    assert (method, path) == ("POST", "/mobile/DepartingTrains")
    assert kwargs["json"] == {"codeGare": "231"}

    client.news("ar")
    method, path, _ = http.calls[-1]
    assert (method, path) == ("GET", "/media/CMS/ar/news")

    client.schedule("200", "490", "2026-11-27T10:00:00.000Z")
    method, path, kwargs = http.calls[-1]
    assert (method, path) == ("POST", "/mobile/schedule")
    assert kwargs["json"]["dateDepartAller"] == "2026-11-27T10:00:00+00:00"


def test_mobile_client_rejects_error_envelope():
    client = MobileOncfClient()
    client.http = FakeHttp({"head": {"errorCode": 999, "errorMessage": "nope"}, "body": None})
    try:
        client.departing_trains("231")
    except Exception as exc:
        assert "999" in str(exc)
    else:
        raise AssertionError("expected an API error")


def test_extract_mobile_news():
    notices = extract_news(load("mobile_news_fr.json"), "fr")
    assert len(notices) >= 4
    first = notices[0]
    assert "Travaux" in first["title"]
    assert first["summary"]
    assert "<" not in first["text"]
    assert first["url"].endswith("/info-travaux")
    assert first["start_at"]
