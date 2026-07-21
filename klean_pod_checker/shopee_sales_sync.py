"""Import the newest Shopee Sell Report downloaded by the Pi's Shopee job."""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .config import Settings
from .multiple_tracking_sync import import_multiple_tracking_sheet
from .sheets_sync import GoogleSheetsWriter, SheetSyncError
from .sheets import parse_order_date
from .shopee import extract_shopee_tracking, parse_shopee_mapping_rows
from .shopee_report import import_shopee_report
from .storage import StatusCache


LOGGER = logging.getLogger(__name__)
# This integration deliberately has its own folder and profile.  Do not point
# it at the unrelated multi-shop Shopee automation project.
DEFAULT_REPORT_DIRECTORY = Path(
    os.environ.get(
        "SHOPEE_REPORT_DIRECTORY",
        "/home/milk/kleanandkare-shopee/sales-reports",
    )
)
DEFAULT_REPORT_GLOB = os.environ.get("SHOPEE_REPORT_GLOB", "Order.all.*.xlsx")
DEFAULT_REPORT_MANIFEST = Path(
    os.environ.get(
        "SHOPEE_REPORT_MANIFEST",
        "/home/milk/kleanandkare-shopee/sales-reports/latest-report-manifest.json",
    )
)
MAPPING_SHEET_RETENTION_DAYS = 45
MAPPING_ARCHIVE_RETENTION_DAYS = 90


@dataclass(frozen=True)
class ShopeeSalesSyncResult:
    report_path: Path
    imported_references: int
    imported_multiple_tracking_references: int = 0
    report_paths: tuple[Path, ...] = ()
    mapping_rows: tuple[tuple[str, str], ...] = ()


def retained_mapping_rows(
    rows: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    cutoff: date,
) -> list[tuple[str, str]]:
    """Keep dated KEX/InterExpress mappings on or after the cutoff."""
    retained: dict[tuple[str, str], None] = {}
    for order_number, raw_tracking in rows:
        order_date = parse_order_date(order_number)
        if order_date is None or order_date < cutoff:
            continue
        # KLEAN&KARE does not need Shopee order mapping. Unknown/raw carrier
        # values are excluded as well so the sheet contains actionable refs.
        if extract_shopee_tracking(raw_tracking) is None:
            continue
        retained[(order_number.upper(), raw_tracking.strip())] = None
    return sorted(retained, key=lambda item: (item[0], item[1]))


def newest_report(report_directory: Path, report_glob: str) -> Path:
    """Return the latest non-empty Sell Report for the configured Shopee shop."""
    reports = [
        path
        for path in report_directory.glob(report_glob)
        if path.is_file() and path.stat().st_size > 1_000
    ]
    if not reports:
        raise FileNotFoundError(
            f"ไม่พบรายงาน Shopee ({report_glob}) ใน {report_directory}"
        )
    return max(reports, key=lambda path: path.stat().st_mtime)


def reports_from_manifest(manifest_path: Path, report_directory: Path) -> tuple[Path, ...]:
    """Return every XLSX part produced by the newest Shopee ZIP download."""
    if not manifest_path.is_file():
        return ()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        LOGGER.warning("Ignoring invalid Shopee report manifest: %s", error)
        return ()

    root = report_directory.resolve()
    reports: list[Path] = []
    for raw_path in payload.get("reports", []):
        candidate = Path(str(raw_path))
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".xlsx" and resolved.stat().st_size > 1_000:
            reports.append(resolved)
    return tuple(reports)


def sync_latest_report(
    *,
    report_directory: Path,
    report_glob: str,
    database_path: Path,
    manifest_path: Path | None = None,
    sheet_id: str = "",
) -> ShopeeSalesSyncResult:
    reports = reports_from_manifest(manifest_path, report_directory) if manifest_path else ()
    if not reports:
        reports = (newest_report(report_directory, report_glob),)
    imported_references = sum(import_shopee_report(report, database_path) for report in reports)
    current_mapping_rows = list(
        dict.fromkeys(
            mapping
            for report in reports
            for mapping in parse_shopee_mapping_rows(report)
        )
    )
    cache = StatusCache(database_path)
    try:
        today = datetime.now().astimezone().date()
        merged_rows = list(
            dict.fromkeys(cache.get_shopee_mapping_rows() + current_mapping_rows)
        )
        archive_cutoff = today - timedelta(days=MAPPING_ARCHIVE_RETENTION_DAYS - 1)
        archive_rows = retained_mapping_rows(merged_rows, cutoff=archive_cutoff)
        cache.replace_shopee_mapping_rows(archive_rows)
        cache.prune_shopee_tracking_refs_before(archive_cutoff.strftime("%y%m%d"))
        sheet_cutoff = today - timedelta(days=MAPPING_SHEET_RETENTION_DAYS - 1)
        mapping_rows = tuple(retained_mapping_rows(archive_rows, cutoff=sheet_cutoff))
    finally:
        cache.close()
    imported_multiple_tracking_references = 0
    if sheet_id:
        try:
            imported_multiple_tracking_references = import_multiple_tracking_sheet(
                sheet_id, database_path
            )
        except (OSError, ValueError, requests.RequestException) as error:
            LOGGER.warning("Grouped tracking sheet sync failed: %s", error)
    return ShopeeSalesSyncResult(
        report_path=reports[-1],
        imported_references=imported_references,
        imported_multiple_tracking_references=imported_multiple_tracking_references,
        report_paths=reports,
        mapping_rows=mapping_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="นำเข้าข้อมูลเลขออเดอร์และเลขพัสดุ KEX/InterExpress จาก Shopee Sell Report ล่าสุด"
    )
    parser.add_argument("--report-directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    parser.add_argument("--report-glob", default=DEFAULT_REPORT_GLOB)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_REPORT_MANIFEST)
    parser.add_argument(
        "--sheet-id",
        default=os.environ.get("SHEET_ID", ""),
        help="Google Sheet ที่มีแท็บ 1 Order หลาย Tracking",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/status-cache.sqlite"),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = sync_latest_report(
            report_directory=args.report_directory,
            report_glob=args.report_glob,
            database_path=args.database,
            manifest_path=args.manifest,
            sheet_id=args.sheet_id,
        )
    except (FileNotFoundError, ValueError) as error:
        LOGGER.error("Shopee Sell Report sync failed: %s", error)
        raise SystemExit(2) from error

    mapping_updated = 0
    settings = Settings.from_env(require_credentials=False)
    writer = GoogleSheetsWriter(settings)
    try:
        if writer.enabled:
            try:
                mapping_updated = writer.replace_mapping_rows(result.mapping_rows)
            except (SheetSyncError, requests.RequestException) as error:
                LOGGER.warning("Mapping Order sheet update failed: %s", error)
    finally:
        writer.close()
    LOGGER.info(
        "Shopee Sell Report synced: reports=%s imported=%s grouped_tracking=%s mapping_order=%s",
        ",".join(path.name for path in result.report_paths),
        result.imported_references,
        result.imported_multiple_tracking_references,
        mapping_updated,
    )


if __name__ == "__main__":
    main()
