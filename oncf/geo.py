"""Geodesy helpers for the live map (no third-party dependencies)."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def cumulative_distances(points: list[tuple[float, float]]) -> list[float]:
    """Distance from the first point to each point, in metres."""
    if not points:
        return []
    result = [0.0]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        result.append(result[-1] + haversine_m(lat1, lon1, lat2, lon2))
    return result


def nearest_point_index(points: list[tuple[float, float]], lat: float, lon: float) -> int:
    best_index, best_distance = 0, float("inf")
    for index, (plat, plon) in enumerate(points):
        distance = haversine_m(lat, lon, plat, plon)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def point_at_distance(
    points: list[tuple[float, float]], cumdist: list[float], distance: float
) -> tuple[float, float]:
    """Interpolate a point at ``distance`` metres along the polyline."""
    if not points:
        raise ValueError("empty polyline")
    if distance <= 0:
        return points[0]
    if distance >= cumdist[-1]:
        return points[-1]
    # Linear scan is fine: Moroccan trip shapes are a few thousand points.
    low, high = 0, len(cumdist) - 1
    while low < high - 1:
        mid = (low + high) // 2
        if cumdist[mid] <= distance:
            low = mid
        else:
            high = mid
    segment = cumdist[high] - cumdist[low]
    fraction = 0.0 if segment == 0 else (distance - cumdist[low]) / segment
    lat = points[low][0] + (points[high][0] - points[low][0]) * fraction
    lon = points[low][1] + (points[high][1] - points[low][1]) * fraction
    return lat, lon


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def simplify_polyline(
    points: list[tuple[float, float]], tolerance: float = 0.008
) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker simplification (tolerance in degrees)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        x1, y1 = points[start]
        x2, y2 = points[end]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best_distance, best_index = -1.0, -1
        for index in range(start + 1, end):
            x, y = points[index]
            if norm == 0:
                distance = math.hypot(x - x1, y - y1)
            else:
                distance = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if distance > best_distance:
                best_distance, best_index = distance, index
        if best_distance > tolerance and best_index > 0:
            keep[best_index] = True
            stack.append((start, best_index))
            stack.append((best_index, end))
    return [point for point, kept in zip(points, keep) if kept]
