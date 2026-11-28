"""Calculation tests required by the brief, section 11.1."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from oncf.calculator import stop_duration, summarize_stop
from oncf.models import DataType, Station, Stop, StopKind
from oncf.timeutil import (
    TZ,
    clock_duration_minutes,
    gtfs_time_to_datetime,
    parse_aware,
    parse_local,
)

STATION = Station(code="380", name_fr="FES", name_ar="فاس")
SERVICE_DATE = date(2026, 11, 23)


def stop(arrival: datetime | None, departure: datetime | None, sequence: int = 3, total: int = 5) -> Stop:
    return Stop(
        station=STATION,
        sequence=sequence,
        arrival=arrival,
        departure=departure,
        status=DataType.SCHEDULED_OFFICIAL,
        total_stops=total,
    )


def at(hour: int, minute: int, day: int = 23) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(
    ("arrival", "departure", "expected"),
    [
        (at(10, 10), at(10, 25), 15),
        (at(10, 10), at(10, 12), 2),
        (at(23, 55), at(0, 10, day=24), 15),
        (at(1, 7), at(1, 40), 33),
        (at(22, 55), at(0, 35, day=24), 100),
    ],
)
def test_duration_in_minutes(arrival, departure, expected):
    assert stop(arrival, departure).duration_minutes == expected


def test_overnight_stop_is_not_negative():
    result = stop_duration(at(23, 55), at(0, 10, day=24))
    assert result == timedelta(minutes=15)
    assert result.total_seconds() > 0


def test_missing_arrival_is_unavailable():
    s = stop(None, at(10, 25))
    assert s.duration is None
    assert s.duration_minutes is None
    assert s.duration_label == "Departure only"


def test_missing_departure_is_unavailable():
    s = stop(at(10, 10), None)
    assert s.duration is None
    assert s.duration_label == "Arrival only"


def test_origin_has_departure_only():
    s = stop(None, at(21, 7), sequence=1, total=5)
    assert s.kind is StopKind.ORIGIN
    assert s.duration_minutes is None
    assert s.duration_label == "Departure only"


def test_destination_has_arrival_only():
    s = stop(at(7, 11), None, sequence=5, total=5)
    assert s.kind is StopKind.DESTINATION
    assert s.duration_minutes is None
    assert s.duration_label == "Arrival only"


def test_pass_through_has_no_scheduled_stop():
    s = stop(None, None)
    assert s.kind is StopKind.PASS_THROUGH
    assert s.duration_label == "No scheduled stop"


def test_zero_minute_stop_is_flagged_not_assumed():
    summary = summarize_stop(stop(at(10, 10), at(10, 10)), SERVICE_DATE)
    assert summary.duration_minutes == 0
    assert any("rounding" in w or "pass-through" in w for w in summary.warnings)


def test_malformed_timestamps_return_none_instead_of_raising():
    assert parse_local("2026-13-01T00:00:00") is None
    assert parse_local("2026-02-30T00:00:00") is None
    assert parse_aware("not-a-date") is None
    assert gtfs_time_to_datetime(SERVICE_DATE, "bad") is None
    assert gtfs_time_to_datetime(SERVICE_DATE, "10:99:00") is None


def test_sub_minute_stop_rounds_to_zero_with_warning():
    summary = summarize_stop(stop(at(10, 10), at(10, 10) + timedelta(seconds=30)), SERVICE_DATE)
    assert summary.duration_minutes == 0
    assert any("less than one minute" in w for w in summary.warnings)


def test_negative_duration_is_rejected_not_rolled_over():
    s = stop(at(10, 10), at(9, 55))
    assert s.duration is None


def test_summary_day_offsets():
    summary = summarize_stop(stop(at(1, 7, day=24), at(1, 40, day=24)), SERVICE_DATE)
    assert summary.arrival_day_offset == 1
    assert summary.departure_day_offset == 1
    assert summary.duration_minutes == 33


def test_summary_serialises():
    payload = summarize_stop(stop(at(1, 7, day=24), at(1, 40, day=24)), SERVICE_DATE).as_dict()
    assert payload["arrival_time"] == "01:07"
    assert payload["departure_time"] == "01:40"
    assert payload["duration_minutes"] == 33
    assert payload["kind"] == "intermediate"


# --- timezone / parsing ------------------------------------------------


def test_oncf_timestamp_keeps_wall_clock_and_ignores_fake_offset():
    parsed = parse_local("2026-11-27T21:07:00+00:00")
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (21, 7)
    assert parsed.tzinfo is not None
    # Morocco moved permanently to UTC+0 on 2026-09-20.
    assert parsed.utcoffset() == timedelta(0)


def test_morocco_is_utc_plus_one_before_september_2026():
    before = datetime(2026, 9, 19, 12, 0, tzinfo=TZ)
    assert before.utcoffset() == timedelta(hours=1)


def test_morocco_is_utc_zero_from_20_september_2026():
    for day in (20, 21, 30):
        moment = datetime(2026, 9, day, 12, 0, tzinfo=TZ)
        assert moment.utcoffset() == timedelta(0), day
    november = datetime(2026, 11, 26, 12, 0, tzinfo=TZ)
    assert november.utcoffset() == timedelta(0)


def test_gtfs_extended_hours_roll_into_next_day():
    parsed = gtfs_time_to_datetime(date(2026, 11, 23), "25:41:00")
    assert parsed == datetime(2026, 11, 24, 1, 41, tzinfo=TZ)


def test_clock_duration_simple():
    assert clock_duration_minutes(time(10, 10), time(10, 25)) == 15


def test_clock_duration_across_midnight():
    assert clock_duration_minutes(time(23, 55), time(0, 10)) == 15


def test_clock_duration_refuses_implausible_rollover():
    # 10:00 -> 09:00 could be 23 h; refuse rather than invent a day.
    assert clock_duration_minutes(time(10, 0), time(9, 0)) is None
