"""Synchronize grouped split-shipment tracking numbers from Google Sheets."""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from .shopee import parse_multiple_tracking_sheet
from .storage import StatusCache


MULTIPLE_TRACKING_SHEET_NAME = "1 Order หลาย Tracking"


def import_multiple_tracking_sheet(
    sheet_id: str,
    database_path: Path,
    *,
    timeout: float = 30,
    session: requests.Session | None = None,
) -> int:
    """Import only order-to-ANB links from the dedicated grouped-tracking tab."""
    clean_sheet_id = str(sheet_id or "").strip()
    if not clean_sheet_id:
        return 0
    client = session or requests.Session()
    response = client.get(
        f"https://docs.google.com/spreadsheets/d/{clean_sheet_id}/export?format=xlsx",
        timeout=timeout,
        headers={"User-Agent": "KleanPodChecker/1.0"},
    )
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as workbook:
        workbook.write(response.content)
        workbook.flush()
        references = parse_multiple_tracking_sheet(
            Path(workbook.name), sheet_name=MULTIPLE_TRACKING_SHEET_NAME
        )
    cache = StatusCache(database_path)
    try:
        cache.put_shopee_tracking_refs(references)
    finally:
        cache.close()
    return len(references)
