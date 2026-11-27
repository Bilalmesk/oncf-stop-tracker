"""Service-brand mapping tests."""
from __future__ import annotations

from oncf.products import from_code, from_name


def test_official_product_codes():
    assert from_code("10002") == ("Al Boraq", "#E82030")
    assert from_code("58") == ("Al Atlas", "#F87820")
    assert from_code("48") == ("TNR", "#4A20AA")
    assert from_code("1000") == ("Supratours", "#E9C900")
    assert from_code("99999") == ("", "")
    assert from_code(None) == ("", "")


def test_gtfs_product_names():
    assert from_name("Al Boraq") == ("Al Boraq", "#E82030")
    assert from_name("Al Atlas") == ("Al Atlas", "#F87820")
    assert from_name("TNR") == ("TNR", "#4A20AA")
    assert from_name("Al Bidaoui") == ("Al Bidaoui", "#4A20AA")
    assert from_name("") == ("", "")
