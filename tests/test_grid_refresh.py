"""Unit tests for the automatic timetable-PDF page parser and check cadence."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from oncf.config import ONCF_BASE_URL
from oncf.grid_refresh import refresh_due, timetable_links


def test_timetable_links_pairs_the_current_heading_with_each_pdf():
    payload = {
        "pages": [
            {"pageSlug": "autre", "pageBlocks": []},
            {
                "pageSlug": "telechargement-des-horaires",
                "pageBlocks": [
                    {"blockName": "titre", "props": {"content": "TNR : Casa - Kénitra"}},
                    {"blockName": "bouton", "props": {"button": {"link": "https://x/Casa-Kenitra.pdf"}}},
                    {"blockName": "titre", "props": {"content": "Al Atlas : Casa - Oriental"}},
                    {"blockName": "bouton", "props": {"button": {"link": "https://x/Casa-Oriental.pdf"}}},
                    {"blockName": "bouton", "props": {"button": {"link": "https://x/not-a-pdf.jpg"}}},
                ],
            },
        ]
    }

    links = timetable_links(payload)

    assert [item["title"] for item in links] == [
        "TNR : Casa - Kénitra",
        "Al Atlas : Casa - Oriental",
    ]
    assert [item["url"] for item in links] == [
        f"{ONCF_BASE_URL}/Casa-Kenitra.pdf",
        f"{ONCF_BASE_URL}/Casa-Oriental.pdf",
    ]


def test_refresh_due_respects_the_manifest_timestamp(tmp_path):
    status = tmp_path / "grid-refresh.json"
    now = datetime.now(timezone.utc)
    status.write_text(json.dumps({"checked_at": now.isoformat()}), encoding="utf-8")
    assert refresh_due(24, status) is False

    old = now - timedelta(hours=25)
    status.write_text(json.dumps({"checked_at": old.isoformat()}), encoding="utf-8")
    assert refresh_due(24, status) is True

    missing = tmp_path / "missing.json"
    assert refresh_due(24, missing) is True

