from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from .models import JobResult


KEX_TRACKING_RE = re.compile(r"ANB[A-Z0-9]{6,}", re.IGNORECASE)
TRACK_URL = "https://th.kex-express.com/th/track/"
VERIFY_URL = "https://th.kex-express.com/track/api/Shipment/VerifyPhone"
ALLOWED_PROOF_HOST_SUFFIX = ".myhuaweicloud.com"
MAX_PROOF_BYTES = 12 * 1024 * 1024


class KexError(RuntimeError):
    pass


class KexClient:
    """Read KEX tracking data and keep POD images server-side."""

    def __init__(
        self,
        pin: str,
        proof_dir: Path,
        *,
        timeout: float = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.pin = pin.strip()
        self.proof_dir = Path(proof_dir)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; FulfilmentDeliveryChecker/1.0)",
                "Accept-Language": "th-TH,th;q=0.9,en;q=0.7",
            }
        )

    def fork(self) -> "KexClient":
        return KexClient(self.pin, self.proof_dir, timeout=self.timeout)

    def search_order(self, tracking_number: str) -> JobResult:
        tracking = _normalize_tracking(tracking_number)
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = self._fetch_tracking([tracking])
        item = next(
            (
                value
                for value in payload
                if str(value.get("tracking_no", "")).upper() == tracking
            ),
            None,
        )
        ref = item.get("ref") if isinstance(item, dict) else None
        if not isinstance(ref, dict) or not isinstance(ref.get("shipment"), dict):
            return JobResult(order_number=tracking, found=False, checked_at=checked_at)

        shipment = ref["shipment"]
        statuses = ref.get("shipment_status") or []
        latest = statuses[0] if statuses and isinstance(statuses[0], dict) else {}
        icon = ref.get("icon") if isinstance(ref.get("icon"), dict) else {}
        display = icon.get("display") if isinstance(icon.get("display"), list) else []
        current_idx = icon.get("current_idx")
        current_icon = (
            display[current_idx]
            if isinstance(current_idx, int) and 0 <= current_idx < len(display)
            else {}
        )

        status_code = str(latest.get("s_code") or current_icon.get("code") or "")
        status_th = str(latest.get("s_desc") or current_icon.get("desc") or "ไม่ทราบสถานะ")
        location = _latest_status_location(statuses)
        delivered = status_code.upper() == "POD" or str(current_icon.get("code")) == "400"
        status_en = "Delivered" if delivered else status_th

        proof_sources = _proof_sources(shipment)
        proof_urls: list[str] = []
        driver = ""
        if proof_sources:
            verified = self._verify_pin(tracking)
            driver = verified.get("driver", "")
            proof_urls = [
                self._download_proof(tracking, source, index)
                for index, source in enumerate(proof_sources, start=1)
            ]

        return JobResult(
            order_number=tracking,
            found=True,
            status_code=status_code,
            status_en=status_en,
            status_th=status_th,
            delivered=delivered,
            driver=driver,
            group_name="KEX",
            created_at=str(shipment.get("pickup_date") or ""),
            delivery_at=str(latest.get("s_datetime") or ""),
            updated_at=str(latest.get("s_datetime") or ""),
            location=location,
            proof_urls=proof_urls,
            checked_at=checked_at,
            raw={},
        )

    def _fetch_tracking(self, tracking_numbers: list[str]) -> list[dict]:
        token = _track_token(tracking_numbers)
        url = f"{TRACK_URL}?track={quote(token, safe='')}"
        response = self.session.post(
            url,
            json={"tracking_no": ",".join(tracking_numbers)},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://th.kex-express.com",
                "Referer": url,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise KexError("KEX ส่งข้อมูลสถานะที่อ่านไม่ได้") from exc
        if isinstance(payload, dict) and payload.get("needCaptcha"):
            raise KexError("KEX ขอการยืนยัน CAPTCHA กรุณาลองใหม่ภายหลัง")
        if not isinstance(payload, list):
            raise KexError("รูปแบบข้อมูลสถานะจาก KEX ไม่ถูกต้อง")
        return payload

    def _verify_pin(self, tracking: str) -> dict[str, str]:
        if not re.fullmatch(r"\d{4}", self.pin):
            raise KexError("ยังไม่ได้ตั้งค่า KEX_PROOF_PIN 4 หลัก")
        response = self.session.post(
            VERIFY_URL,
            json={"consignmentNo": tracking, "verifyCode": self.pin},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://th.kex-express.com",
                "Referer": TRACK_URL,
                "kett-lang": "th",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise KexError("KEX ส่งผลยืนยัน PIN ที่อ่านไม่ได้") from exc
        verify_status = payload.get("verifySta") if isinstance(payload, dict) else None
        if not isinstance(verify_status, dict) or str(verify_status.get("code")) != "01":
            raise KexError("PIN สำหรับดูหลักฐาน KEX ไม่ถูกต้อง")

        driver = ""
        for row in payload.get("verifyShipment_Status") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("courier_name") or "").strip()
            if name:
                driver = name
                break
        return {"driver": driver}

    def _download_proof(self, tracking: str, source_url: str, index: int) -> str:
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host.endswith(ALLOWED_PROOF_HOST_SUFFIX):
            raise KexError("KEX ส่งลิงก์หลักฐานจากแหล่งที่ไม่อนุญาต")

        response = self.session.get(source_url, stream=True, timeout=self.timeout)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        extensions = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        extension = extensions.get(content_type)
        if extension is None:
            raise KexError("ไฟล์หลักฐาน KEX ไม่ใช่รูปภาพที่รองรับ")

        target_dir = self.proof_dir / tracking
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:10]
        filename = f"proof-{index}-{digest}{extension}"
        target = target_dir / filename
        if not target.exists():
            descriptor, temp_name = tempfile.mkstemp(prefix=".proof-", dir=target_dir)
            size = 0
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_PROOF_BYTES:
                            raise KexError("รูปหลักฐาน KEX มีขนาดใหญ่เกินกำหนด")
                        handle.write(chunk)
                if size == 0:
                    raise KexError("รูปหลักฐาน KEX เป็นไฟล์ว่าง")
                os.replace(temp_name, target)
            except Exception:
                Path(temp_name).unlink(missing_ok=True)
                raise
        return f"/proof/kex/{tracking}/{filename}"

    def close(self) -> None:
        self.session.close()


def _normalize_tracking(value: str) -> str:
    tracking = re.sub(r"\s+", "", value or "").upper()
    if not KEX_TRACKING_RE.fullmatch(tracking):
        raise ValueError("เลขพัสดุ KEX ต้องขึ้นต้นด้วย ANB")
    return tracking


def _track_token(tracking_numbers: list[str]) -> str:
    tracking = ",".join(tracking_numbers)
    value = "fHx8".join(
        [secrets.token_hex(16), secrets.token_hex(16), tracking, secrets.token_hex(16), secrets.token_hex(16)]
    )
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _proof_sources(shipment: dict) -> list[str]:
    values: list[str] = []
    photos = shipment.get("epod_photo")
    if isinstance(photos, list):
        values.extend(str(value) for value in photos if isinstance(value, str) and value)
    for key in ("sig", "img"):
        value = shipment.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            values.append(value)
    return list(dict.fromkeys(values))


def _latest_status_location(statuses: list) -> str:
    rows = [row for row in statuses if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("s_datetime") or ""), reverse=True)
    for row in rows:
        location = str(row.get("loc") or "").strip()
        if location:
            return location
    return ""
