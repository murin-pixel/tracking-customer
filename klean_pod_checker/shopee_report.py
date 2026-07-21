from __future__ import annotations

import argparse
from pathlib import Path

from .shopee import parse_shopee_report
from .storage import StatusCache


def import_shopee_report(report_path: Path, database_path: Path) -> int:
    references = parse_shopee_report(report_path)
    cache = StatusCache(database_path)
    try:
        cache.put_shopee_tracking_refs(references)
    finally:
        cache.close()
    return len(references)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="นำเข้าเฉพาะหมายเลขออเดอร์และเลขพัสดุ KEX/InterExpress จาก Shopee Sell Report"
    )
    parser.add_argument("report", type=Path, help="ไฟล์ Order.all.*.xlsx จาก Shopee")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/status-cache.sqlite"),
        help="ตำแหน่งฐานข้อมูลของระบบ",
    )
    args = parser.parse_args()
    imported = import_shopee_report(args.report, args.database)
    print(f"Imported {imported} Shopee order-to-tracking references")


if __name__ == "__main__":
    main()
