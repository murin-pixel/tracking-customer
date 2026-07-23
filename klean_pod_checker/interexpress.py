from __future__ import annotations

import re
from datetime import datetime

import requests

from .models import JobResult


INTEREXPRESS_TRACKING_RE = re.compile(r"ANB[A-Z0-9]{6,}", re.IGNORECASE)
API_BASE_URL = "https://api-intership.interexpress.co.th/v1"


class InterexpressError(RuntimeError):
    pass


class InterexpressClient:
    """Read status from the authorized InterExpress corporate account."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        timeout: float = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.username = username.strip()
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "FulfilmentDeliveryChecker/1.0",
            }
        )
        self._access_token = ""

    def fork(self) -> "InterexpressClient":
        return InterexpressClient(self.username, self.password, timeout=self.timeout)

    def login(self) -> None:
        if not self.username or not self.password:
            raise InterexpressError(
                "ยังไม่ได้ตั้งค่า INTEREXPRESS_USERNAME หรือ INTEREXPRESS_PASSWORD"
            )
        response = self.session.post(
            f"{API_BASE_URL}/users/login",
            json={
                "email": self.username,
                "password": self.password,
                "type": "corporate",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise InterexpressError("InterExpress ส่งผลเข้าสู่ระบบที่อ่านไม่ได้") from exc
        token = (payload.get("token") or {}).get("accessToken")
        if not isinstance(token, str) or not token:
            raise InterexpressError("เข้าสู่ระบบ InterExpress ไม่สำเร็จ")
        self._access_token = token

    def search_order(self, tracking_number: str) -> JobResult:
        tracking = _normalize_tracking(tracking_number)
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for attempt in range(2):
            if not self._access_token:
                self.login()
            response = self.session.get(
                f"{API_BASE_URL}/track/trace/{tracking}",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=self.timeout,
            )
            if response.status_code == 401 and attempt == 0:
                self._access_token = ""
                continue
            if response.status_code == 404:
                return JobResult(order_number=tracking, found=False, checked_at=checked_at)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise InterexpressError("InterExpress ส่งข้อมูลสถานะที่อ่านไม่ได้") from exc
            if not isinstance(payload, dict) or not payload.get("shipmentNo"):
                return JobResult(order_number=tracking, found=False, checked_at=checked_at)

            status_code = str(payload.get("lastStatusCode") or "")
            status_th = str(payload.get("ttDisplayRemarks") or "ไม่ทราบสถานะ")
            delivery_at = str(payload.get("actDeliveryDt") or "")
            latest_detail = _latest_tracking_detail(payload)
            location = str(latest_detail.get("dcThName") or "").strip()
            delivered = (
                status_code.upper() == "POD"
                or "จัดส่งสำเร็จ" in status_th
                or bool(delivery_at)
            )
            return JobResult(
                order_number=tracking,
                found=True,
                status_code=status_code,
                status_en="Delivered" if delivered else status_code,
                status_th=status_th,
                delivered=delivered,
                group_name="InterExpress",
                created_at=str(payload.get("actPickupDt") or payload.get("estPickupDt") or ""),
                delivery_at=delivery_at,
                updated_at=str(payload.get("lastStatusDt") or delivery_at),
                location=location,
                checked_at=checked_at,
                raw={},
            )
        raise InterexpressError("เชื่อมต่อ InterExpress ไม่สำเร็จ")

    def close(self) -> None:
        self.session.close()


def _normalize_tracking(value: str) -> str:
    tracking = re.sub(r"\s+", "", value or "").upper()
    if not INTEREXPRESS_TRACKING_RE.fullmatch(tracking):
        raise ValueError("เลขพัสดุ InterExpress ต้องขึ้นต้นด้วย ANB")
    return tracking


def _latest_tracking_detail(payload: dict) -> dict:
    details = payload.get("shipmentTrackingDetail")
    if not isinstance(details, list):
        return {}
    rows = [row for row in details if isinstance(row, dict)]
    if not rows:
        return {}
    return max(rows, key=lambda row: str(row.get("trackingDt") or ""))
