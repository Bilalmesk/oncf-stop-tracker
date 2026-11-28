"""Clock parsing used by official grid verification."""
from __future__ import annotations

from oncf.grid import parse_clock


def test_parse_clock_accepts_real_clock_times():
    assert parse_clock("00:00") == 0
    assert parse_clock("7:40") == 460
    assert parse_clock("23:59") == 23 * 60 + 59


def test_parse_clock_rejects_out_of_range_clock_times():
    for value in ("24:00", "25:00", "99:99", "18:60", "-1:00", "abc", ""):
        assert parse_clock(value) is None
