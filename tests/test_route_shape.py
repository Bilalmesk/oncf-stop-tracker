from __future__ import annotations

from oncf.gtfs import GtfsFeed


class DummyFeed:
    def __init__(self, shape):
        self.shape = shape

    def network(self, tolerance: float = 0.008):
        return [
            {
                "coach": False,
                "product": "Al Boraq",
                "shape": self.shape,
            }
        ]


FORWARD = [[33.5, -7.6], [34.0, -6.5], [35.7, -5.8]]


def test_route_shape_between_trims_forward():
    shape = GtfsFeed.route_shape_between(
        DummyFeed(FORWARD), 33.59, -7.59, 35.77, -5.79, product="Al Boraq"
    )
    assert shape == [(33.5, -7.6), (34.0, -6.5), (35.7, -5.8)]


def test_route_shape_between_reverses_when_needed():
    shape = GtfsFeed.route_shape_between(
        DummyFeed(list(reversed(FORWARD))), 33.59, -7.59, 35.77, -5.79, product="Al Boraq"
    )
    assert shape == [(33.5, -7.6), (34.0, -6.5), (35.7, -5.8)]


def test_route_shape_between_rejects_far_endpoints():
    shape = GtfsFeed.route_shape_between(
        DummyFeed(FORWARD), 33.59, -7.59, 35.77, -5.79, max_km=1
    )
    assert shape == []


def test_route_shape_between_without_product_match_falls_back_to_rail():
    class OtherProduct(DummyFeed):
        def network(self, tolerance: float = 0.008):
            route = super().network(tolerance)[0]
            route["product"] = "TNR"
            return [route]

    shape = GtfsFeed.route_shape_between(
        OtherProduct(FORWARD), 33.59, -7.59, 35.77, -5.79, product="Al Boraq"
    )
    assert shape == [(33.5, -7.6), (34.0, -6.5), (35.7, -5.8)]
