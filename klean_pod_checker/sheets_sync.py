from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin

import requests

from .config import Settings
from .models import JobResult, OrderRef
from .proof_tokens import make_sheet_proof_token


class SheetSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SheetUpdate:
    row: int
    order_number: str
    status: str
    checked_at: str
    proof_urls: list[str]


class GoogleSheetsWriter:
    """Write delivery results and Shopee mappings through an Apps Script webhook."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.sheet_id = settings.sheet_id
        self.sheet_gid = settings.sheet_gid
        self.webhook_url = settings.google_sheets_webhook_url
        self._secret = settings.google_sheets_webhook_secret
        self.timeout = settings.request_timeout_seconds
        self.public_base_url = settings.public_base_url
        self.web_secret_key = settings.web_secret_key
        self.session = session or requests.Session()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url and self._secret)

    def update_report_rows(
        self, rows: Iterable[tuple[OrderRef, JobResult]]
    ) -> int:
        updates: list[SheetUpdate] = []
        for ref, result in rows:
            for row_number in ref.sheet_rows:
                updates.append(
                    _build_update(
                        row_number,
                        result,
                        self.public_base_url,
                        self.web_secret_key,
                        carrier=ref.carrier,
                    )
                )
        return self._send(updates)

    def update_rows(
        self,
        row_numbers: Iterable[int],
        result: JobResult,
        *,
        carrier: str = "",
    ) -> int:
        updates = [
            _build_update(
                row_number,
                result,
                self.public_base_url,
                self.web_secret_key,
                carrier=carrier,
            )
            for row_number in row_numbers
        ]
        return self._send(updates)

    def _send(self, updates: list[SheetUpdate]) -> int:
        if not self.enabled or not updates:
            return 0
        with self._lock:
            response = self.session.post(
                self.webhook_url,
                json={
                    "secret": self._secret,
                    "sheet_id": self.sheet_id,
                    "sheet_gid": self.sheet_gid,
                    "updates": [
                        {
                            "row": item.row,
                            "order_number": item.order_number,
                            "status": item.status,
                            "checked_at": item.checked_at,
                            "proof_urls": item.proof_urls,
                        }
                        for item in updates
                    ],
                },
                timeout=self.timeout,
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise SheetSyncError("Google Apps Script ส่งผลลัพธ์ที่ไม่ใช่ JSON") from exc
        if not payload.get("ok"):
            raise SheetSyncError(str(payload.get("error") or "เขียน Google Sheet ไม่สำเร็จ"))
        return int(payload.get("updated") or 0)

    def replace_mapping_rows(self, rows: Iterable[tuple[str, str]]) -> int:
        """Replace only columns A and P in the ``Mapping Order`` tab."""
        if not self.enabled:
            return 0
        clean_rows = list(
            dict.fromkeys(
                (
                    str(order_number).strip().upper(),
                    str(tracking_number).strip(),
                )
                for order_number, tracking_number in rows
                if str(order_number).strip() and str(tracking_number).strip()
            )
        )
        with self._lock:
            response = self.session.post(
                self.webhook_url,
                json={
                    "action": "replace_mapping_order",
                    "secret": self._secret,
                    "sheet_id": self.sheet_id,
                    "sheet_name": "Mapping Order",
                    "rows": clean_rows,
                },
                timeout=max(self.timeout, 60),
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise SheetSyncError("Google Apps Script ส่งผลลัพธ์ที่ไม่ใช่ JSON") from exc
        if not payload.get("ok"):
            raise SheetSyncError(str(payload.get("error") or "เขียน Mapping Order ไม่สำเร็จ"))
        if payload.get("action") != "replace_mapping_order":
            raise SheetSyncError("Google Apps Script ยังไม่ได้อัปเดตฟังก์ชัน Mapping Order")
        return int(payload.get("mapping_updated") or 0)

    def close(self) -> None:
        self.session.close()


def _build_update(
    row_number: int,
    result: JobResult,
    public_base_url: str = "",
    web_secret_key: str = "",
    *,
    carrier: str = "",
) -> SheetUpdate:
    if result.error:
        status = "ตรวจสอบผิดพลาด"
    elif not result.found:
        status = "ไม่พบในระบบขนส่ง"
    else:
        label = result.status_th or result.status_en or "ไม่ทราบสถานะ"
        status = f"{label} ({result.status_code})" if result.status_code else label
    # Skyfrog returns two signature captures before the delivery photos.  They
    # are private customer data, so never send them to the Google Sheet.
    proof_sources = result.proof_urls[2:] if carrier == "skyfrog" else result.proof_urls
    return SheetUpdate(
        row=max(2, int(row_number)),
        order_number=result.order_number,
        status=status,
        checked_at=result.checked_at,
        proof_urls=list(
            dict.fromkeys(
                filter(
                    None,
                    (
                _sheet_proof_url(value, public_base_url, web_secret_key)
                for value in proof_sources
                    ),
                )
            )
        ),
    )


def _sheet_proof_url(value: str, public_base_url: str, web_secret_key: str) -> str:
    if public_base_url and web_secret_key:
        try:
            token = make_sheet_proof_token(value, web_secret_key)
        except ValueError:
            return ""
        return f"{public_base_url}/sheet-proof/{token}"
    return (
        urljoin(public_base_url + "/", value.lstrip("/"))
        if value.startswith("/") and public_base_url
        else value
    )
