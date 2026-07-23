from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .config import Settings
from .shopee import ShopeeTrackingRef, extract_shopee_tracking
from .supabase_mapping import SupabaseMappingStore


def read_legacy_references(database_path: Path) -> list[ShopeeTrackingRef]:
    connection = sqlite3.connect(database_path)
    try:
        references: dict[tuple[str, str], ShopeeTrackingRef] = {}
        if _table_exists(connection, "shopee_order_tracking"):
            for order_number, tracking_number, carrier in connection.execute(
                "SELECT order_number, tracking_number, carrier FROM shopee_order_tracking"
            ):
                item = ShopeeTrackingRef(
                    str(order_number).upper(),
                    str(tracking_number).upper(),
                    str(carrier).lower(),
                )
                references[(item.order_number, item.tracking_number)] = item
        if _table_exists(connection, "shopee_mapping_order"):
            for order_number, raw_tracking in connection.execute(
                "SELECT order_number, tracking_number FROM shopee_mapping_order"
            ):
                extracted = extract_shopee_tracking(str(raw_tracking))
                if extracted is None:
                    continue
                carrier, tracking_number = extracted
                item = ShopeeTrackingRef(
                    str(order_number).upper(), tracking_number, carrier
                )
                key = (item.order_number, item.tracking_number)
                existing = references.get(key)
                if existing is None or existing.carrier == "auto":
                    references[key] = item
        return sorted(
            references.values(),
            key=lambda item: (item.order_number, item.tracking_number),
        )
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ย้าย Shopee Mapping เดิมจาก SQLite ไป Supabase หนึ่งครั้ง"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/status-cache.sqlite"),
    )
    args = parser.parse_args()

    references = read_legacy_references(args.database)
    settings = Settings.from_env(require_credentials=False)
    store = SupabaseMappingStore(
        settings.supabase_url,
        settings.supabase_secret_key,
        table=settings.supabase_mapping_table,
    )
    try:
        imported = store.upsert_references(references)
    finally:
        store.close()
    print(f"Migrated {imported} Shopee mappings from SQLite to Supabase")


if __name__ == "__main__":
    main()
