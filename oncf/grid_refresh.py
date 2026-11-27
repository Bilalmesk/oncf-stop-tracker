"""Automatically refresh official ONCF timetable PDFs.

The official CMS page (``telechargement-des-horaires``) exposes a title and a
PDF button for every line. This module downloads those PDFs into a separate
``auto-current`` directory, parses them with the same grid parser used by the
rest of the app, and promotes the new set only when at least eight sheets parse
and their effective date is not older than the current set.

It is deliberately conservative: a failed/partial download never replaces a
working timetable, and the original ``official-2026-10`` folder is never
modified (tests pin that folder).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AUTO_GRID_DIR, CACHE_DIR, GRID_DIR, ONCF_BASE_URL
from .grid import GridLibrary, parse_grid_pdf

TIMETABLE_SLUG = "telechargement-des-horaires"
MIN_VALID_SHEETS = 8


def _pages(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("pages", "page"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _gateway_url(url: str) -> str:
    """Keep only the path and put it behind the configured API gateway."""
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return f"{ONCF_BASE_URL}{parsed.path}"
    return url


def timetable_links(payload: Any) -> list[dict[str, str]]:
    """Return title + PDF URL pairs from the official timetable CMS page."""
    page = next(
        (
            item
            for item in _pages(payload)
            if item.get("pageSlug") == TIMETABLE_SLUG
        ),
        None,
    )
    if page is None:
        return []

    items: list[dict[str, str]] = []
    title = ""
    for block in page.get("pageBlocks") or []:
        if not isinstance(block, dict):
            continue
        props = block.get("props") or {}
        if block.get("blockName") == "titre":
            title = str(props.get("content") or title)
        elif block.get("blockName") == "bouton":
            link = str((props.get("button") or {}).get("link") or "")
            if link.lower().split("?")[0].endswith(".pdf"):
                items.append({"title": title, "url": _gateway_url(link)})

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def _filename(index: int, url: str) -> str:
    raw = url.rsplit("/", 1)[-1].split("?", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or "timetable.pdf"
    if not clean.lower().endswith(".pdf"):
        clean += ".pdf"
    return f"{index:02d}-{clean}"


def _latest_effective(library: GridLibrary) -> date | None:
    dates = [grid.effective for grid in library.grids if grid.effective]
    return max(dates) if dates else None


def read_status(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or (CACHE_DIR / "grid-refresh.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh_due(hours: float, path: Path | None = None) -> bool:
    status = read_status(path)
    checked = status.get("checked_at")
    if not checked:
        return True
    try:
        when = datetime.fromisoformat(checked)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when).total_seconds()
        return age >= hours * 3600
    except (TypeError, ValueError):
        return True


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def refresh_timetable_pdfs(
    client: Any,
    target_dir: Path | None = None,
    *,
    current_effective: date | None = None,
    status_path: Path | None = None,
) -> dict[str, Any]:
    """Download the current official PDFs and promote them when valid.

    Returns a JSON-friendly status plus ``library`` (a GridLibrary) when the
    promoted directory is usable.
    """
    target = Path(target_dir or AUTO_GRID_DIR)
    status_path = Path(status_path or (CACHE_DIR / "grid-refresh.json"))
    checked_at = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    try:
        payload = client.request_json("GET", "/media/CMS/fr/pages")
        links = timetable_links(payload)
    except Exception as exc:  # noqa: BLE001 - reported to the UI
        status = {
            "checked_at": checked_at,
            "effective": current_effective.isoformat() if current_effective else None,
            "source": "auto",
            "promoted": False,
            "error": f"timetable page: {type(exc).__name__}: {exc}"[:500],
        }
        _write_status(status_path, status)
        return status

    if len(links) < MIN_VALID_SHEETS:
        status = {
            "checked_at": checked_at,
            "effective": current_effective.isoformat() if current_effective else None,
            "source": "auto",
            "promoted": False,
            "error": f"only {len(links)} timetable PDF links found",
        }
        _write_status(status_path, status)
        return status

    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    valid: list[dict[str, Any]] = []
    grids = []
    for index, link in enumerate(links, start=1):
        path = staging / _filename(index, link["url"])
        try:
            content, _headers = client.request_bytes("GET", link["url"])
            if not content.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            path.write_bytes(content)
            grid = parse_grid_pdf(path)
            if not grid.sections or not grid.effective:
                raise ValueError("no parseable timetable sections/effective date")
            grids.append(grid)
            valid.append(
                {
                    "file": path.name,
                    "title": link.get("title") or grid.title,
                    "effective": grid.effective.isoformat(),
                    "trains": len(grid.trains()),
                    "sections": len(grid.sections),
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep downloading the rest
            errors.append(f"{link['url'].rsplit('/', 1)[-1]}: {type(exc).__name__}: {exc}"[:300])
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    if len(valid) < MIN_VALID_SHEETS:
        shutil.rmtree(staging, ignore_errors=True)
        status = {
            "checked_at": checked_at,
            "effective": current_effective.isoformat() if current_effective else None,
            "source": "auto",
            "promoted": False,
            "valid_sheets": len(valid),
            "errors": errors,
            "error": f"only {len(valid)} valid sheets (need {MIN_VALID_SHEETS})",
        }
        _write_status(status_path, status)
        return status

    latest = max(item["effective"] for item in valid)
    latest_date = date.fromisoformat(latest)
    if current_effective is not None and latest_date < current_effective:
        shutil.rmtree(staging, ignore_errors=True)
        status = {
            "checked_at": checked_at,
            "effective": current_effective.isoformat(),
            "source": "auto",
            "promoted": False,
            "valid_sheets": len(valid),
            "errors": errors,
            "error": f"downloaded timetables are older ({latest}) than current ({current_effective})",
        }
        _write_status(status_path, status)
        return status

    try:
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
        promoted = True
        library = GridLibrary.load_dir(target)
        if not len(library):
            raise ValueError("promoted directory has no parseable sheets")
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        status = {
            "checked_at": checked_at,
            "effective": current_effective.isoformat() if current_effective else None,
            "source": "auto",
            "promoted": False,
            "valid_sheets": len(valid),
            "errors": errors,
            "error": f"promote failed: {type(exc).__name__}: {exc}"[:500],
        }
        _write_status(status_path, status)
        return status

    status = {
        "checked_at": checked_at,
        "effective": latest,
        "source": "auto",
        "promoted": promoted,
        "valid_sheets": len(valid),
        "files": valid,
        "errors": errors,
    }
    _write_status(status_path, status)
    status["library"] = library
    return status
