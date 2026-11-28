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


def test_float_minutes_are_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.add(kind="departure_delay", train="MA", minutes=10.9)


def test_list_rejects_non_iso_date(tmp_path):
    store = make_store(tmp_path)
    store.add(kind="other", train="MA", date="2026-11-27")
    with pytest.raises(ValueError):
        store.add(kind="other", train="MA", date="20261127")
    with pytest.raises(ValueError):
        store.list(date="20261127")
    assert len(store.list(date="2026-11-27")) == 1


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


def test_confirm_and_dispute_votes_from_two_clients(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")

    confirmed = store.vote(report["id"], "confirm", client_id="client-aaa")
    disputed = store.vote(report["id"], "dispute", client_id="client-bbb")

    assert confirmed["confirmations"] == 1
    assert disputed["confirmations"] == 1
    assert disputed["disputes"] == 1


def test_vote_tolerates_corrupt_stored_counts(tmp_path):
    path = tmp_path / "reports.json"
    path.write_text(
        """[
          {
            "id": "corrupt1",
            "kind": "other",
            "train": "MA",
            "created_at": "2026-11-27T11:00:00+00:00",
            "expires_at": "2026-11-27T17:00:00+00:00",
            "confirmations": "abc",
            "disputes": null,
            "votes": {}
          }
        ]""",
        encoding="utf-8",
    )
    store = ReportStore(path, now=lambda: NOW)
    report = store.vote("corrupt1", "confirm", client_id="client-aaa")
    assert report["confirmations"] == 1
    assert report["disputes"] == 0


def test_repeating_the_same_vote_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")

    first = store.vote(report["id"], "confirm", client_id="client-aaa")
    second = store.vote(report["id"], "confirm", client_id="client-aaa")

    assert first["confirmations"] == 1
    assert second["confirmations"] == 1
    assert second["my_vote"] == "confirm"


def test_changing_vote_moves_the_count_without_double_counting(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")

    store.vote(report["id"], "confirm", client_id="client-aaa")
    switched = store.vote(report["id"], "dispute", client_id="client-aaa")

    assert switched["confirmations"] == 0
    assert switched["disputes"] == 1
    assert switched["my_vote"] == "dispute"


def test_two_clients_are_counted_independently(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")

    store.vote(report["id"], "confirm", client_id="client-aaa")
    both = store.vote(report["id"], "confirm", client_id="client-bbb")

    assert both["confirmations"] == 2
    assert both["my_vote"] == "confirm"


def test_vote_requires_a_valid_client_id(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="other", train="MA")

    with pytest.raises(ValueError):
        store.vote(report["id"], "confirm")
    with pytest.raises(ValueError):
        store.vote(report["id"], "confirm", client_id="bad id!")


def test_list_hides_other_voters_but_returns_my_vote(tmp_path):
    store = make_store(tmp_path)
    report = store.add(kind="cancellation", train="103")
    store.vote(report["id"], "confirm", client_id="client-aaa")
    store.vote(report["id"], "dispute", client_id="client-bbb")

    mine = store.list(train="103", client_id="client-aaa")[0]
    anonymous = store.list(train="103")[0]

    assert mine["my_vote"] == "confirm"
    assert "votes" not in mine
    assert anonymous["my_vote"] is None


def test_vote_rejects_unknown_report_and_bad_value(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.vote("missing", "confirm", client_id="client-aaa")

    report = store.add(kind="other", train="MA")
    with pytest.raises(ValueError):
        store.vote(report["id"], "maybe", client_id="client-aaa")


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
