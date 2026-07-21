from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .models import JobResult

if TYPE_CHECKING:
    from .shopee import ShopeeTrackingRef


class StatusCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS status_cache (
                order_number TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status_code TEXT NOT NULL,
                is_final INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shopee_order_tracking (
                order_number TEXT NOT NULL,
                tracking_number TEXT NOT NULL,
                carrier TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (order_number, tracking_number)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS shopee_order_tracking_order_idx "
            "ON shopee_order_tracking (order_number)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shopee_mapping_order (
                order_number TEXT NOT NULL,
                tracking_number TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (order_number, tracking_number)
            )
            """
        )
        self.connection.commit()

    def get_final(self, order_number: str) -> JobResult | None:
        row = self.connection.execute(
            "SELECT payload_json FROM status_cache WHERE order_number = ? AND is_final = 1",
            (order_number,),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        return JobResult(**payload)

    def get(self, order_number: str) -> JobResult | None:
        row = self.connection.execute(
            "SELECT payload_json FROM status_cache WHERE order_number = ?",
            (order_number,),
        ).fetchone()
        if not row:
            return None
        return JobResult(**json.loads(row[0]))

    def put(self, result: JobResult) -> None:
        payload = asdict(result)
        self.connection.execute(
            """
            INSERT INTO status_cache (order_number, payload_json, status_code, is_final, checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(order_number) DO UPDATE SET
                payload_json = excluded.payload_json,
                status_code = excluded.status_code,
                is_final = excluded.is_final,
                checked_at = excluded.checked_at
            """,
            (
                result.order_number,
                json.dumps(payload, ensure_ascii=False),
                result.status_code,
                int(result.final),
                result.checked_at,
            ),
        )
        self.connection.commit()

    def put_shopee_tracking_refs(self, references: list["ShopeeTrackingRef"]) -> None:
        imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.connection.executemany(
            """
            INSERT INTO shopee_order_tracking
                (order_number, tracking_number, carrier, imported_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(order_number, tracking_number) DO UPDATE SET
                carrier = CASE
                    WHEN excluded.carrier = 'auto'
                        AND shopee_order_tracking.carrier IN ('kex', 'interexpress')
                    THEN shopee_order_tracking.carrier
                    ELSE excluded.carrier
                END,
                imported_at = excluded.imported_at
            """,
            [
                (item.order_number.upper(), item.tracking_number.upper(), item.carrier, imported_at)
                for item in references
            ],
        )
        self.connection.commit()

    def get_shopee_tracking_refs(self, order_number: str) -> list[tuple[str, str]]:
        rows = self.connection.execute(
            """
            SELECT tracking_number, carrier
            FROM shopee_order_tracking
            WHERE order_number = ?
            ORDER BY tracking_number
            """,
            (order_number.upper(),),
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def get_shopee_order_for_tracking(self, tracking_number: str) -> str | None:
        """Return the order linked to a tracking number imported from Shopee."""
        row = self.connection.execute(
            """
            SELECT order_number
            FROM shopee_order_tracking
            WHERE tracking_number = ?
            ORDER BY imported_at DESC, order_number
            LIMIT 1
            """,
            (tracking_number.upper(),),
        ).fetchone()
        return str(row[0]) if row else None

    def put_shopee_mapping_rows(self, rows: list[tuple[str, str]]) -> None:
        """Add immutable order-to-tracking pairs to the Mapping Order archive."""
        imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.connection.executemany(
            """
            INSERT INTO shopee_mapping_order (order_number, tracking_number, imported_at)
            VALUES (?, ?, ?)
            ON CONFLICT(order_number, tracking_number) DO UPDATE SET
                imported_at = excluded.imported_at
            """,
            [
                (str(order).strip().upper(), str(tracking).strip(), imported_at)
                for order, tracking in rows
                if str(order).strip() and str(tracking).strip()
            ],
        )
        self.connection.commit()

    def get_shopee_mapping_rows(self) -> list[tuple[str, str]]:
        rows = self.connection.execute(
            """
            SELECT order_number, tracking_number
            FROM shopee_mapping_order
            ORDER BY order_number, tracking_number
            """
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def replace_shopee_mapping_rows(self, rows: list[tuple[str, str]]) -> None:
        """Replace the retained Mapping Order archive in one transaction."""
        imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        clean_rows = list(
            dict.fromkeys(
                (str(order).strip().upper(), str(tracking).strip())
                for order, tracking in rows
                if str(order).strip() and str(tracking).strip()
            )
        )
        with self.connection:
            self.connection.execute("DELETE FROM shopee_mapping_order")
            self.connection.executemany(
                """
                INSERT INTO shopee_mapping_order
                    (order_number, tracking_number, imported_at)
                VALUES (?, ?, ?)
                """,
                [(order, tracking, imported_at) for order, tracking in clean_rows],
            )

    def prune_shopee_tracking_refs_before(self, order_date_key: str) -> int:
        """Remove imported Shopee references older than the YYMMDD cutoff."""
        with self.connection:
            cursor = self.connection.execute(
                """
                DELETE FROM shopee_order_tracking
                WHERE SUBSTR(order_number, 1, 6) < ?
                """,
                (order_date_key,),
            )
        return int(cursor.rowcount)

    def get_shopee_mapping_values(self, order_number: str) -> list[str]:
        """Return the raw Shopee column-P values linked to one order."""
        rows = self.connection.execute(
            """
            SELECT tracking_number
            FROM shopee_mapping_order
            WHERE order_number = ?
            ORDER BY tracking_number
            """,
            (order_number.upper(),),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def get_shopee_mapping_order_for_tracking(self, tracking_number: str) -> str | None:
        """Return the Mapping Order order linked to a carrier tracking number."""
        row = self.connection.execute(
            """
            SELECT order_number
            FROM shopee_mapping_order
            WHERE UPPER(tracking_number) LIKE ?
            ORDER BY imported_at DESC, order_number
            LIMIT 1
            """,
            (f"%{tracking_number.upper()}%",),
        ).fetchone()
        return str(row[0]) if row else None

    def close(self) -> None:
        self.connection.close()
