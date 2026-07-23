from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from .shopee import ShopeeTrackingRef


class SupabaseMappingError(RuntimeError):
    pass


class SupabaseMappingStore:
    """Read and write Shopee order mappings through Supabase PostgREST."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        table: str = "shopee_order_mapping",
        timeout: float = 15,
        session: requests.Session | None = None,
    ) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise ValueError("ชื่อตาราง Supabase ไม่ถูกต้อง")
        self.url = url.strip().rstrip("/")
        self.secret_key = secret_key.strip()
        self.table = table
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret_key)

    @property
    def endpoint(self) -> str:
        return f"{self.url}/rest/v1/{quote(self.table, safe='')}"

    def get_tracking_refs(self, order_number: str) -> list[tuple[str, str]]:
        if not self.enabled:
            return []
        response = self._request(
            "GET",
            params={
                "select": "tracking_number,carrier",
                "order_number": f"eq.{order_number.strip().upper()}",
                "order": "tracking_number.asc",
            },
        )
        payload = self._json_rows(response)
        return [
            (str(row["tracking_number"]).upper(), str(row["carrier"]).lower())
            for row in payload
            if row.get("tracking_number") and row.get("carrier")
        ]

    def get_order_for_tracking(self, tracking_number: str) -> str | None:
        if not self.enabled:
            return None
        response = self._request(
            "GET",
            params={
                "select": "order_number",
                "tracking_number": f"eq.{tracking_number.strip().upper()}",
                "order": "order_number.desc",
                "limit": "1",
            },
        )
        payload = self._json_rows(response)
        if not payload or not payload[0].get("order_number"):
            return None
        return str(payload[0]["order_number"]).upper()

    def list_references_since(self, order_date_key: str) -> list[ShopeeTrackingRef]:
        if not self.enabled:
            return []
        rows: list[dict[str, Any]] = []
        page_size = 1_000
        for offset in range(0, 100_000, page_size):
            response = self._request(
                "GET",
                params={
                    "select": "order_number,tracking_number,carrier",
                    "order_number": f"gte.{order_date_key}",
                    "order": "order_number.asc,tracking_number.asc",
                },
                headers={"Range": f"{offset}-{offset + page_size - 1}"},
            )
            page = self._json_rows(response)
            rows.extend(page)
            if len(page) < page_size:
                break
        return [
            ShopeeTrackingRef(
                str(row["order_number"]).upper(),
                str(row["tracking_number"]).upper(),
                str(row["carrier"]).lower(),
            )
            for row in rows
            if row.get("order_number") and row.get("tracking_number") and row.get("carrier")
        ]

    def upsert_references(self, references: Iterable[ShopeeTrackingRef]) -> int:
        if not self.enabled:
            raise SupabaseMappingError("ยังไม่ได้ตั้งค่า SUPABASE_URL หรือ SUPABASE_SECRET_KEY")
        unique: dict[tuple[str, str], ShopeeTrackingRef] = {}
        for item in references:
            order_number = item.order_number.strip().upper()
            tracking_number = item.tracking_number.strip().upper()
            carrier = item.carrier.strip().lower()
            if not order_number or not tracking_number or carrier not in {
                "auto",
                "kex",
                "interexpress",
            }:
                continue
            key = (order_number, tracking_number)
            existing = unique.get(key)
            if existing is None or (existing.carrier == "auto" and carrier != "auto"):
                unique[key] = ShopeeTrackingRef(order_number, tracking_number, carrier)

        known = [item for item in unique.values() if item.carrier != "auto"]
        automatic = [item for item in unique.values() if item.carrier == "auto"]
        self._upsert_batches(known, resolution="merge-duplicates")
        self._upsert_batches(automatic, resolution="ignore-duplicates")
        return len(unique)

    def prune_before(self, order_date_key: str) -> int:
        if not self.enabled:
            raise SupabaseMappingError("ยังไม่ได้ตั้งค่า SUPABASE_URL หรือ SUPABASE_SECRET_KEY")
        response = self._request(
            "DELETE",
            params={"order_number": f"lt.{order_date_key}", "select": "order_number"},
            headers={"Prefer": "return=representation"},
        )
        return len(self._json_rows(response))

    def close(self) -> None:
        self.session.close()

    def _upsert_batches(
        self, references: list[ShopeeTrackingRef], *, resolution: str
    ) -> None:
        imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for offset in range(0, len(references), 500):
            batch = references[offset : offset + 500]
            self._request(
                "POST",
                params={"on_conflict": "order_number,tracking_number"},
                json=[
                    {
                        "order_number": item.order_number,
                        "tracking_number": item.tracking_number,
                        "carrier": item.carrier,
                        "imported_at": imported_at,
                    }
                    for item in batch
                ],
                headers={"Prefer": f"resolution={resolution},return=minimal"},
            )

    def _request(self, method: str, **kwargs: Any) -> requests.Response:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": self.secret_key,
            **kwargs.pop("headers", {}),
        }
        # Legacy service-role keys are JWTs; new sb_secret keys use apikey only.
        if self.secret_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.secret_key}"
        try:
            response = self.session.request(
                method,
                self.endpoint,
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise SupabaseMappingError("เชื่อมต่อ Supabase Mapping ไม่สำเร็จ") from error
        return response

    @staticmethod
    def _json_rows(response: requests.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise SupabaseMappingError("Supabase ส่งข้อมูลที่อ่านไม่ได้") from error
        if not isinstance(payload, list):
            raise SupabaseMappingError("รูปแบบข้อมูล Supabase Mapping ไม่ถูกต้อง")
        return [row for row in payload if isinstance(row, dict)]
