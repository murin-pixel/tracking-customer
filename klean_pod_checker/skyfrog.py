from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

import requests

from .models import JobResult


STATUS_LABELS = {
    "B": ("Open", "เปิดงาน"),
    "R": ("Received", "รับสินค้าแล้ว"),
    "S": ("Sent", "ออกนำส่ง"),
    "A": ("Completed (By Admin)", "จัดส่งสำเร็จ (แอดมิน)"),
    "C": ("Completed", "จัดส่งสำเร็จ"),
    "E": ("Rejected", "ปฏิเสธ/จัดส่งไม่สำเร็จ"),
    "P": ("Rejected", "ปฏิเสธ/จัดส่งไม่สำเร็จ"),
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


class SkyfrogError(RuntimeError):
    pass


class SkyfrogClient:
    def __init__(
        self,
        customer_code: str,
        username: str,
        password: str,
        *,
        timeout: float = 30,
        request_delay: float = 0.1,
        session: requests.Session | None = None,
    ) -> None:
        self.customer_code = customer_code
        self.username = username
        self._password = password
        self.timeout = timeout
        self.request_delay = request_delay
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) KleanPodChecker/1.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.user_id = ""
        self.view_image_base = "https://www.skyfrog.net/store/"

    def login(self) -> None:
        url = "https://www.skyfrog.net/vrp/Controllers/Common/Login.ashx"
        response = self.session.post(
            url,
            data={
                "c": self.customer_code,
                "u": self.username,
                "p": self._password,
                "r": "false",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = _json(response)
        if not payload.get("success"):
            message = payload.get("message") or payload.get("messages") or "เข้าสู่ระบบไม่สำเร็จ"
            raise SkyfrogError(str(message))

        profile_response = self.session.get(
            "https://www.skyfrog.net/vrp/Controllers/Common/UserSession.ashx",
            timeout=self.timeout,
        )
        profile_response.raise_for_status()
        profile_payload = _json(profile_response)
        profiles = profile_payload.get("datas") or []
        if not profiles:
            raise SkyfrogError("Skyfrog ไม่ส่งข้อมูล user session หลังเข้าสู่ระบบ")
        profile = profiles[0]
        self.user_id = str(profile.get("userid") or "")
        self.view_image_base = str(
            profile.get("viewimg") or "https://www.skyfrog.net/store/"
        ).rstrip("/") + "/"
        if not self.user_id:
            raise SkyfrogError("ไม่พบ user ID ภายในของ Skyfrog")

    def fork(self) -> "SkyfrogClient":
        session = requests.Session()
        session.headers.update(self.session.headers)
        session.cookies.update(self.session.cookies)
        client = SkyfrogClient(
            self.customer_code,
            self.username,
            self._password,
            timeout=self.timeout,
            request_delay=self.request_delay,
            session=session,
        )
        client.user_id = self.user_id
        client.view_image_base = self.view_image_base
        return client

    def search_order(self, order_number: str) -> JobResult:
        if not self.user_id:
            raise SkyfrogError("ต้อง login ก่อนค้นหาออเดอร์")
        if self.request_delay:
            time.sleep(self.request_delay)

        response = self.session.get(
            "https://www.skyfrog.net/vrp/Controllers/Employee/JobController.ashx",
            params={
                "action": "view",
                "limit": "30",
                "start": "0",
                "ssbpcode": self.customer_code,
                "ssuserid": self.user_id,
                "displayworker": "",
                "query": order_number,
                "columns": json.dumps(["job.jobno"]),
                "istoday": "false",
                "hhid": "",
                "jobstatus": "",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = _json(response)
        if payload.get("success") is False:
            raise SkyfrogError(str(payload.get("message") or "ค้นหา Skyfrog ไม่สำเร็จ"))

        rows = payload.get("datas") or []
        exact = [
            row
            for row in rows
            if _normalize_order(str(row.get("jobno") or "")) == _normalize_order(order_number)
        ]
        if not exact:
            return JobResult(
                order_number=order_number,
                found=False,
                checked_at=_now_iso(),
            )

        row = max(exact, key=_row_sort_key)
        status_code = str(row.get("jobstatus") or "").upper()
        status_en, status_th = STATUS_LABELS.get(
            status_code, (status_code or "Unknown", "ไม่ทราบสถานะ")
        )
        return JobResult(
            order_number=order_number,
            found=True,
            status_code=status_code,
            status_en=status_en,
            status_th=status_th,
            delivered=status_code in {"A", "C"},
            driver=str(row.get("worker") or row.get("ref1") or "").strip(),
            group_name=str(row.get("groupname") or row.get("hgroupname") or "").strip(),
            created_at=str(row.get("cdate") or "").strip(),
            delivery_at=str(row.get("ddate") or "").strip(),
            updated_at=str(row.get("UpdateDate") or "").strip(),
            customer=str(row.get("delivery") or row.get("customer") or "").strip(),
            proof_urls=self._proof_urls(row),
            checked_at=_now_iso(),
            raw=row,
        )

    def _proof_urls(self, row: dict[str, Any]) -> list[str]:
        filenames: list[str] = []
        for key in ("rsignimg", "dsignimg", "rmanimg", "dmanimg", "attachfile"):
            _collect_image_filenames(row.get(key), filenames)
        _collect_image_filenames(row.get("Upload5Pic"), filenames)

        urls: list[str] = []
        seen: set[str] = set()
        base = f"{self.view_image_base}{quote(self.customer_code)}/pod/"
        for filename in filenames:
            clean = filename.strip().replace("\\", "/").split("/")[-1]
            if not clean or clean.casefold() in seen:
                continue
            seen.add(clean.casefold())
            urls.append(base + quote(clean))
        return urls

    def close(self) -> None:
        self.session.close()


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        text = response.text[:200].strip()
        raise SkyfrogError(f"Skyfrog ส่งข้อมูลที่ไม่ใช่ JSON: {text}") from exc
    if not isinstance(payload, dict):
        raise SkyfrogError("รูปแบบข้อมูลจาก Skyfrog ไม่ถูกต้อง")
    return payload


def _normalize_order(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("UpdateDate") or ""),
        str(row.get("ddate") or ""),
        str(row.get("cdate") or ""),
    )


def _collect_image_filenames(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and PurePosixPath(candidate.split("?", 1)[0]).suffix.casefold() in IMAGE_EXTENSIONS:
            output.append(candidate)
        return
    if isinstance(value, dict):
        for child in value.values():
            _collect_image_filenames(child, output)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for child in value:
            _collect_image_filenames(child, output)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
