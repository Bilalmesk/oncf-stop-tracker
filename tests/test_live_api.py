"""HTTP contract tests for trip status and community reports."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

from oncf.live import make_handler


class FakeApp:
    def __init__(self):
        self.calls = []

    def trip_status(self, **kwargs):
        self.calls.append(("trip_status", kwargs))
        if kwargs["train"] == "ZZ999":
            raise LookupError("train ZZ999 does not run between these stations")
        return {
            "train": kwargs["train"],
            "status": "running",
            "status_label": "En route: A → B",
        }

    def destinations(self, **kwargs):
        self.calls.append(("destinations", kwargs))
        return {
            "station": {"code": kwargs["station"], "name": "Station A"},
            "date": kwargs["travel_date"].isoformat(),
            "destinations": [{"code": "C", "name": "Station C", "train": "MA"}],
        }

    def punctuality_summary(self, days=7):
        self.calls.append(("punctuality_summary", days))
        return {"sample_size": 3, "on_time_pct": 66.7}

    def list_reports(self, **kwargs):
        self.calls.append(("reports_list", kwargs))
        return {"reports": [{"id": "abc123", "train": kwargs.get("train")}]}

    def add_report(self, payload, client_ip):
        self.calls.append(("add_report", payload, client_ip))
        return {"id": "new123", **payload, "active": True}

    def vote_report(self, report_id, vote, client_id=None):
        self.calls.append(("vote_report", report_id, vote, client_id))
        return {"id": report_id, "confirmations": 1}


@pytest.fixture()
def fake_server():
    app = FakeApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield app, base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_trip_status_route(fake_server):
    app, base = fake_server
    response = requests.get(
        base + "/api/trip-status",
        params={"train": "103", "from": "200", "to": "490", "date": "2026-11-27"},
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    call = app.calls[0]
    assert call[0] == "trip_status"
    assert call[1]["train"] == "103"
    assert call[1]["origin"] == "200"


def test_trip_status_requires_parameters(fake_server):
    _, base = fake_server
    response = requests.get(base + "/api/trip-status", timeout=5)
    assert response.status_code == 400


def test_list_reports_route(fake_server):
    app, base = fake_server
    response = requests.get(base + "/api/reports", params={"train": "103"}, timeout=5)

    assert response.status_code == 200
    assert response.json()["reports"][0]["id"] == "abc123"
    assert app.calls[0][1]["train"] == "103"


def test_create_report_route(fake_server):
    app, base = fake_server
    response = requests.post(
        base + "/api/reports",
        json={"kind": "departure_delay", "train": "103", "minutes": 20},
        timeout=5,
    )

    assert response.status_code == 201
    assert response.json()["id"] == "new123"
    assert app.calls[0][0] == "add_report"
    assert app.calls[0][1]["minutes"] == 20


def test_vote_report_route(fake_server):
    app, base = fake_server
    response = requests.post(
        base + "/api/reports/abc123/vote",
        json={"vote": "confirm", "client_id": "client-aaa"},
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["confirmations"] == 1
    assert app.calls[0] == ("vote_report", "abc123", "confirm", "client-aaa")


def test_invalid_json_is_rejected(fake_server):
    _, base = fake_server
    response = requests.post(
        base + "/api/reports",
        data="{not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert response.status_code == 400


def test_destinations_route(fake_server):
    app, base = fake_server
    response = requests.get(
        base + "/api/destinations",
        params={"station": "200", "date": "2026-11-27"},
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["destinations"][0]["code"] == "C"
    assert app.calls[0][0] == "destinations"
    assert app.calls[0][1]["station"] == "200"


def test_services_invalid_date_returns_400(fake_server):
    _, base = fake_server
    response = requests.get(
        base + "/api/services",
        params={"from": "200", "to": "490", "date": "2026-13-40"},
        timeout=5,
    )
    assert response.status_code == 400


def test_journey_invalid_date_returns_400(fake_server):
    _, base = fake_server
    response = requests.get(
        base + "/api/journey",
        params={"train": "MA", "from": "200", "to": "490", "date": "nope"},
        timeout=5,
    )
    assert response.status_code == 400


def test_trip_status_unknown_train_returns_404(fake_server):
    _, base = fake_server
    response = requests.get(
        base + "/api/trip-status",
        params={"train": "ZZ999", "from": "200", "to": "490", "date": "2026-11-27"},
        timeout=5,
    )
    assert response.status_code == 404


def test_verify_requires_valid_time(fake_server):
    _, base = fake_server
    missing = requests.get(base + "/api/verify", timeout=5)
    malformed = requests.get(
        base + "/api/verify",
        params={"train": "301", "station": "OUJDA", "time": "not-a-time"},
        timeout=5,
    )
    assert missing.status_code == 400
    assert malformed.status_code == 400


def test_reports_invalid_date_returns_400(fake_server):
    _, base = fake_server
    response = requests.get(base + "/api/reports", params={"date": "notadate"}, timeout=5)
    assert response.status_code == 400


def test_punctuality_route(fake_server):
    app, base = fake_server
    response = requests.get(base + "/api/punctuality", params={"days": 14}, timeout=5)

    assert response.status_code == 200
    assert response.json()["sample_size"] == 3
    assert app.calls[0] == ("punctuality_summary", 14)
