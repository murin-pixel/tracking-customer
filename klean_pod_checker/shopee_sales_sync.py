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
from .shopee import (
    ShopeeTrackingRef,
    extract_shopee_tracking,
    parse_shopee_report,
)
from .supabase_mapping import SupabaseMappingError, SupabaseMappingStore


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
SUPABASE_RETENTION_DAYS = 60


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


def mapping_rows_from_references(
    references: list[ShopeeTrackingRef],
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in references:
        if item.carrier == "kex":
            label = f"KEX_เลขพัสดุ_{item.tracking_number}"
        elif item.carrier == "interexpress":
            label = f"INTEREXPRESS เลขพัสดุ {item.tracking_number}"
        else:
            continue
        rows.append((item.order_number, label))
    return sorted(dict.fromkeys(rows), key=lambda item: (item[0], item[1]))


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
    mapping_store: SupabaseMappingStore,
    manifest_path: Path | None = None,
    sheet_id: str = "",
) -> ShopeeSalesSyncResult:
    reports = reports_from_manifest(manifest_path, report_directory) if manifest_path else ()
    if not reports:
        reports = (newest_report(report_directory, report_glob),)
    references = list(
        {
            (item.order_number, item.tracking_number): item
            for report in reports
            for item in parse_shopee_report(report)
        }.values()
    )
    imported_references = mapping_store.upsert_references(references)
    today = datetime.now().astimezone().date()
    retention_cutoff = today - timedelta(days=SUPABASE_RETENTION_DAYS - 1)
    mapping_store.prune_before(retention_cutoff.strftime("%y%m%d"))
    imported_multiple_tracking_references = 0
    if sheet_id:
        try:
            imported_multiple_tracking_references = import_multiple_tracking_sheet(
                sheet_id, mapping_store
            )
        except (
            OSError,
            ValueError,
            SupabaseMappingError,
            requests.RequestException,
        ) as error:
            LOGGER.warning("Grouped tracking sheet sync failed: %s", error)
    sheet_cutoff = today - timedelta(days=MAPPING_SHEET_RETENTION_DAYS - 1)
    supabase_references = mapping_store.list_references_since(
        sheet_cutoff.strftime("%y%m%d")
    )
    mapping_rows = tuple(mapping_rows_from_references(supabase_references))
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env(require_credentials=False)
    mapping_store = SupabaseMappingStore(
        settings.supabase_url,
        settings.supabase_secret_key,
        table=settings.supabase_mapping_table,
    )
    try:
        result = sync_latest_report(
            report_directory=args.report_directory,
            report_glob=args.report_glob,
            mapping_store=mapping_store,
            manifest_path=args.manifest,
            sheet_id=args.sheet_id,
        )
    except (
        FileNotFoundError,
        ValueError,
        SupabaseMappingError,
        requests.RequestException,
    ) as error:
        LOGGER.error("Shopee Sell Report sync failed: %s", error)
        raise SystemExit(2) from error
    finally:
        mapping_store.close()

    mapping_updated = 0
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
