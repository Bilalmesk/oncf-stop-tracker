"""ONCF service brands (Al Boraq, Al Atlas, TNR, Supratours)."""
from __future__ import annotations

PRODUCTS_BY_CODE: dict[str, tuple[str, str]] = {
    "10002": ("Al Boraq", "#E82030"),
    "58": ("Al Atlas", "#F87820"),
    "48": ("TNR", "#4A20AA"),
    "1000": ("Supratours", "#E9C900"),
}

PRODUCT_COLORS: dict[str, str] = {
    "Al Boraq": "#E82030",
    "Al Atlas": "#F87820",
    "TNR": "#4A20AA",
    "Al Bidaoui": "#4A20AA",
    "Supratours": "#E9C900",
}


def from_code(code: str | None) -> tuple[str, str]:
    """Return ``(product_name, colour)`` for an ONCF ``codeGamme``."""
    if not code:
        return "", ""
    return PRODUCTS_BY_CODE.get(str(code).strip(), ("", ""))


def from_name(name: str | None) -> tuple[str, str]:
    """Return ``(product_name, colour)`` for a GTFS ``route_short_name``."""
    clean = (name or "").strip()
    if not clean:
        return "", ""
    return clean, PRODUCT_COLORS.get(clean, "")
