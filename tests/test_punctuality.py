"""Tests for local punctuality observations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oncf.punctuality import PunctualityLog

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def make_departures():
    return [
        {
            "train": "103",
            "scheduled": "06:40:00",
            "real": "06:43:00",
            "delay_minutes": 3,
            "delay_level": 1,
        },
        {
            "train": "MA",
            "scheduled": "21:07:00",
            "real": "21:25:00",
            "delay_minutes": 18,
            "delay_level": 3,
        },
    ]


def test_records_new_departures(tmp_path):
    log = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    assert log.record("200", make_departures()) == 2
    summary = log.summary(days=7)

    assert summary["sample_size"] == 2
    assert summary["on_time"] == 1
    assert summary["delayed"] == 1
    assert summary["severe"] == 1
    assert summary["max_delay"] == 18


def test_unchanged_observation_is_not_logged_twice(tmp_path):
    log = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    log.record("200", make_departures())

    assert log.record("200", make_departures()) == 0
    assert len(log.observations(days=7)) == 2


def test_changed_delay_is_logged_and_latest_wins(tmp_path):
    log = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    log.record("200", make_departures())
    changed = make_departures()
    changed[0]["real"] = "07:01:00"
    changed[0]["delay_minutes"] = 21
    changed[0]["delay_level"] = 3

    assert log.record("200", changed) == 1
    summary = log.summary(days=7)
    assert summary["sample_size"] == 2
    assert summary["severe"] == 2
    assert summary["max_delay"] == 21


def test_thresholds_and_average(tmp_path):
    log = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    log.record(
        "200",
        [
            {"train": "A", "scheduled": "10:00:00", "real": "10:05:00", "delay_minutes": 5, "delay_level": 1},
            {"train": "B", "scheduled": "11:00:00", "real": "11:06:00", "delay_minutes": 6, "delay_level": 2},
            {"train": "C", "scheduled": "12:00:00", "real": "12:16:00", "delay_minutes": 16, "delay_level": 3},
        ],
    )
    summary = log.summary(days=7)

    assert summary["on_time"] == 1
    assert summary["delayed"] == 2
    assert summary["severe"] == 1
    assert summary["average_delay"] == 9.0
    assert summary["median_delay"] == 6


def test_old_observations_are_excluded(tmp_path):
    old = PunctualityLog(
        tmp_path / "observations.jsonl",
        now=lambda: NOW - timedelta(days=10),
    )
    old.record("200", make_departures())

    current = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    assert current.summary(days=7)["sample_size"] == 0


def test_empty_and_missing_fields_are_ignored(tmp_path):
    log = PunctualityLog(tmp_path / "observations.jsonl", now=lambda: NOW)
    assert log.record("200", []) == 0
    assert log.record("200", [{"train": "A"}]) == 0


def test_corrupt_lines_do_not_break_the_summary(tmp_path):
    path = tmp_path / "observations.jsonl"
    path.write_text("{bad json\n", encoding="utf-8")
    log = PunctualityLog(path, now=lambda: NOW)
    log.record("200", make_departures())

    assert log.summary(days=7)["sample_size"] == 2
