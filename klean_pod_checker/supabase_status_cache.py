from __future__ import annotations

import re
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from .models import JobResult


class SupabaseStatusCacheError(RuntimeError):
    pass


class SupabaseStatusCache:
    """Store the latest privacy-safe tracking result through Supabase PostgREST."""

    SAFE_FIELDS = {
        "order_number",
        "found",
        "status_code",
        "status_en",
        "status_th",
        "delivered",
        "group_name",
        "created_at",
        "delivery_at",
        "updated_at",
        "location",
        "checked_at",
    }

    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        table: str = "tracking_status_cache",
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
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret_key)

    @property
    def endpoint(self) -> str:
        return f"{self.url}/rest/v1/{quote(self.table, safe='')}"

    def get(self, order_number: str) -> JobResult | None:
        return self._get(order_number)

    def get_final(self, order_number: str) -> JobResult | None:
        return self._get(order_number, final_only=True)

    def put(self, result: JobResult) -> None:
        if not self.enabled:
            raise SupabaseStatusCacheError(
                "ยังไม่ได้ตั้งค่า SUPABASE_URL หรือ SUPABASE_SECRET_KEY"
            )
        checked_at = result.checked_at or datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        payload = {
            key: value
            for key, value in asdict(result).items()
            if key in self.SAFE_FIELDS
        }
        payload["checked_at"] = checked_at
        self._request(
            "POST",
            params={"on_conflict": "tracking_number"},
            json={
                "tracking_number": result.order_number.strip().upper(),
                "payload": payload,
                "status_code": result.status_code,
                "is_final": result.final,
                "checked_at": checked_at,
                "updated_at": checked_at,
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def close(self) -> None:
        self.session.close()

    def _get(
        self, order_number: str, *, final_only: bool = False
    ) -> JobResult | None:
        if not self.enabled:
            return None
        params = {
            "select": "payload",
            "tracking_number": f"eq.{order_number.strip().upper()}",
            "limit": "1",
        }
        if final_only:
            params["is_final"] = "eq.true"
        response = self._request("GET", params=params)
        rows = self._json_rows(response)
        if not rows or not isinstance(rows[0].get("payload"), dict):
            return None
        payload = {
            key: value
            for key, value in rows[0]["payload"].items()
            if key in self.SAFE_FIELDS
        }
        try:
            return JobResult(**payload)
        except TypeError as error:
            raise SupabaseStatusCacheError(
                "รูปแบบข้อมูล status cache ใน Supabase ไม่ถูกต้อง"
            ) from error

    def _request(self, method: str, **kwargs: Any) -> requests.Response:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": self.secret_key,
            **kwargs.pop("headers", {}),
        }
        if self.secret_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.secret_key}"
        try:
            with self._lock:
                response = self.session.request(
                    method,
                    self.endpoint,
                    headers=headers,
                    timeout=self.timeout,
                    **kwargs,
                )
            response.raise_for_status()
        except requests.RequestException as error:
            raise SupabaseStatusCacheError(
                "เชื่อมต่อ Supabase status cache ไม่สำเร็จ"
            ) from error
        return response

    @staticmethod
    def _json_rows(response: requests.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise SupabaseStatusCacheError(
                "Supabase status cache ส่งข้อมูลที่อ่านไม่ได้"
            ) from error
        if not isinstance(payload, list):
            raise SupabaseStatusCacheError(
                "รูปแบบข้อมูล Supabase status cache ไม่ถูกต้อง"
            )
        return [row for row in payload if isinstance(row, dict)]
