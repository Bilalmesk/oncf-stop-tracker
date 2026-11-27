from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oncf import sources


def test_source_status_service_caches(monkeypatch):
    calls = []

    def fake_probe():
        calls.append(1)
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"name": "x", "host": "example", "ok": True}],
            "note": "ok",
        }

    monkeypatch.setattr(sources, "probe_sources", fake_probe)
    service = sources.SourceStatusService(ttl_seconds=300)
    first = service.get()
    second = service.get()
    assert first["checks"][0]["name"] == "x"
    assert second is first
    assert len(calls) == 1

    service._checked_at = datetime.now(timezone.utc) - timedelta(seconds=301)
    service.get(refresh=True)
    assert len(calls) == 2


def test_source_status_service_reports_failure(monkeypatch):
    def fail():
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "probe_sources", fail)
    service = sources.SourceStatusService()
    result = service.get()
    assert result["checks"] == []
    assert "network down" in result["error"]

def test_probe_sources_reports_live_board(monkeypatch):
    monkeypatch.setattr(sources, "_dns", lambda host: ("ok", "1.2.3.4"))

    def fake_http(host, path, *, method="GET", body=None):
        if path.endswith("/cache/getServerTimeZone"):
            return 200, {"serverTimeZone": {"timeZone": "+00:00"}}
        if path.endswith("/mobile/DepartingTrains"):
            return 200, {"head": {"errorCode": 0}, "body": {"trains": []}}
        if path.endswith("/media/CMS/en/news"):
            return 200, {"news": [{"title": "works"}]}
        return None, None

    monkeypatch.setattr(sources, "_http_json", fake_http)
    data = sources.probe_sources()
    checks = {check["name"]: check for check in data["checks"]}
    assert checks["API gateway — timetable and stations"]["ok"] is True
    assert checks["API gateway — live station departures"]["ok"] is True
    assert checks["API gateway — traffic notices"]["ok"] is True
    assert "station departure" in data["note"].lower()


def test_probe_sources_marks_live_board_down(monkeypatch):
    monkeypatch.setattr(sources, "_dns", lambda host: ("ok", "1.2.3.4"))

    def fake_http(host, path, *, method="GET", body=None):
        if path.endswith("/mobile/DepartingTrains"):
            return 200, {"head": {"errorCode": 999}, "body": None}
        return 200, {}

    monkeypatch.setattr(sources, "_http_json", fake_http)
    data = sources.probe_sources()
    checks = {check["name"]: check for check in data["checks"]}
    assert checks["API gateway — live station departures"]["ok"] is False
    assert "No successful" in checks["API gateway — live station departures"]["note"]

