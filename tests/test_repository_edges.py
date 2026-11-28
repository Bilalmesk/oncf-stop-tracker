"""Edge cases in repository time resolution."""
from __future__ import annotations

from datetime import date, datetime, time

from oncf.api import Leg
from oncf.repository import Repository
from oncf.timeutil import TZ


def test_missing_arrival_does_not_pick_previous_day_departure():
    repo = Repository.__new__(Repository)

    def fake_leg(train, origin, destination, travel_date):
        return Leg(
            origin_code=origin,
            destination_code=destination,
            departure=datetime.combine(travel_date, time(9, 0), tzinfo=TZ),
            arrival=datetime.combine(travel_date, time(12, 0), tzinfo=TZ),
        )

    repo._find_leg = fake_leg
    notes: list[str] = []
    departure = repo._resolve_departure(
        "MA", "380", "490", date(2026, 11, 27), None, notes
    )

    assert departure is not None
    assert departure.date() == date(2026, 11, 27)
    assert notes == ["departure time not cross-checked against an arrival time"]
