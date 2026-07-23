from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .shopee import parse_shopee_report
from .supabase_mapping import SupabaseMappingStore


def import_shopee_report(report_path: Path, mapping_store: SupabaseMappingStore) -> int:
    return mapping_store.upsert_references(parse_shopee_report(report_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="นำเข้าหมายเลขออเดอร์และเลขพัสดุ Shopee ไปยัง Supabase"
    )
    parser.add_argument("report", type=Path, help="ไฟล์ Order.all.*.xlsx จาก Shopee")
    args = parser.parse_args()

    settings = Settings.from_env(require_credentials=False)
    store = SupabaseMappingStore(
        settings.supabase_url,
        settings.supabase_secret_key,
        table=settings.supabase_mapping_table,
    )
    try:
        imported = import_shopee_report(args.report, store)
    finally:
        store.close()
    print(f"Imported {imported} Shopee order-to-tracking references to Supabase")


if __name__ == "__main__":
    main()
