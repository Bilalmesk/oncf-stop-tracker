"""Tests for the local community-reports store."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oncf.reports import ReportStore

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def make_store(tmp_path, now=NOW):
    return ReportStore(tmp_path / "reports.json", now=lambda: now)


def test_add_and_list_a_report(tmp_path):
    store = make_store(tmp_path)
    report = store.add(
        kind="cancellation",
        train="103",
        station="380",
        date="2026-11-27",
        message="Train annulé",
    )

    assert report["kind"] == "cancellation"
    assert report["train"] == "103"
    assert report["active"] is True
    assert report["confirmations"] == 0
    assert len(report["id"]) >= 8
    assert store.list(train="103")[0]["id"] == report["id"]


def test_reports_persist_across_instances(tmp_path):
    path = tmp_path / "reports.json"
    first = ReportStore(path, now=lambda: NOW)
    first.add(kind="departure_delay", train="MA", minutes=25)

    second = ReportStore(path, now=lambda: NOW)
    reports = second.list(train="MA")
    assert len(reports) == 1
    assert reports[0]["minutes"] == 25


def test_delay_requires_valid_minutes(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add(kind="departure_delay", train="MA", minutes=0)
    with pytest.raises(ValueError):
        store.add(kind="departure_delay", train="MA", minutes=601)


def test_unknown_kind_and_missing_train_are_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add(kind="teleport", train="MA")
    with pytest.raises(ValueError):
        store.add(kind="cancellation", train="")


def test_message_is_limited(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add(kind="other", train="MA", message="x" * 281)


def test_expired_reports_are_hidden_by_default(tmp_path):
    now = [NOW]
    store = ReportStore(tmp_path / "reports.json", now=lambda: now[0])
    report = store.add(kind="departure_delay", train="MA", minutes=20)

    now[0] = NOW + timedelta(hours=4)

    assert store.list(train="MA") == []
    all_reports = store.list(train="MA", active_only=False)
    assert len(all_reports) == 1
    assert all_reports[0]["id"] == report["id"]
    assert all_reports[0]["active"] is False


def test_confirm_and_dispute_votes(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")

    confirmed = store.vote(report["id"], "confirm")
    disputed = store.vote(report["id"], "dispute")

    assert confirmed["confirmations"] == 1
    assert disputed["disputes"] == 1


def test_vote_rejects_unknown_report_and_bad_value(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.vote("missing", "confirm")

    report = store.add(kind="other", train="MA")
    with pytest.raises(ValueError):
        store.vote(report["id"], "maybe")


def test_filter_by_station_and_date(tmp_path):
    store = make_store(tmp_path)
    store.add(kind="cancellation", train="103", station="380", date="2026-11-27")
    store.add(kind="delay", train="MA", station="200", date="2026-11-28", minutes=10)

    assert len(store.list(station="380")) == 1
    assert len(store.list(date="2026-11-28")) == 1
    assert len(store.list()) == 2


def test_delay_for_trip_uses_the_strongest_active_report(tmp_path):
    store = make_store(tmp_path)
    store.add(kind="departure_delay", train="MA", date="2026-11-27", minutes=10)
    store.add(kind="departure_delay", train="MA", date="2026-11-27", minutes=25)

    minutes, source = store.delay_for_trip("MA", "2026-11-27")
    assert minutes == 25
    assert source == "community"


def test_delay_for_trip_is_none_without_a_report(tmp_path):
    store = make_store(tmp_path)
    assert store.delay_for_trip("MA", "2026-11-27") == (0, "none")
