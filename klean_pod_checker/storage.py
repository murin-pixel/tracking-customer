from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import JobResult


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

    def close(self) -> None:
        self.connection.close()
