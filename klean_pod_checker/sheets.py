from __future__ import annotations

import csv
import io
import re
from datetime import date

import requests

from .models import OrderRef


KLEAN_ORDER_RE = re.compile(
    r"KLEAN\s*&\s*KARE\s*_\s*([0-9]{6}[A-Z0-9]{4,})",
    re.IGNORECASE,
)
ORDER_NUMBER_RE = re.compile(r"[0-9]{6}[A-Z0-9]{4,}", re.IGNORECASE)
KEX_REFERENCE_RE = re.compile(
    r"KEX(?:PRESS)?\s*_\s*(?:เลขพัสดุ\s*_\s*)?(ANB[A-Z0-9]{6,})",
    re.IGNORECASE,
)
KEX_TRACKING_RE = re.compile(r"ANB[A-Z0-9]{6,}", re.IGNORECASE)
INTEREXPRESS_REFERENCE_RE = re.compile(
    r"INTER\s*EXPRESS(?:\s*[*_\-]*เลขพัสดุ[*_\-]*)?\s*(ANB[A-Z0-9]{6,})",
    re.IGNORECASE,
)


def normalize_order_input(value: str) -> str:
    """Return a normalized Skyfrog order or KEX tracking number."""
    return normalize_tracking_input(value)[1]


def normalize_auto_search_input(value: str) -> str:
    """Normalize an input for the CS carrier-priority search.

    The automatic CS search deliberately does not infer one carrier from a
    prefix: it tries the normalized value against Skyfrog, then KEX, then
    InterExpress. This keeps ANB numbers and pasted carrier labels usable
    without making the CS user choose a provider first.
    """
    clean = re.sub(r"\s+", "", value or "")
    if not clean:
        raise ValueError("กรุณาใส่เลขออเดอร์")
    if len(clean) > 80:
        raise ValueError("เลขออเดอร์ยาวเกินไป")

    clean = clean.lstrip("#")
    for pattern in (KLEAN_ORDER_RE, KEX_REFERENCE_RE, INTEREXPRESS_REFERENCE_RE):
        match = pattern.fullmatch(clean)
        if match:
            return match.group(1).upper()

    candidate = clean.upper()
    if ORDER_NUMBER_RE.fullmatch(candidate) or KEX_TRACKING_RE.fullmatch(candidate):
        return candidate
    raise ValueError("รูปแบบไม่ถูกต้อง กรุณาตรวจเลขออเดอร์หรือเลขพัสดุ")


def normalize_tracking_input(value: str, carrier: str = "auto") -> tuple[str, str]:
    """Return (carrier, number) for values accepted by the CS search box.

    Bare ANB labels are ambiguous between KEX and InterExpress, so callers must
    select a carrier unless the pasted sheet value already includes its prefix.
    """
    clean = re.sub(r"\s+", "", value or "")
    if not clean:
        raise ValueError("กรุณาใส่เลขออเดอร์")
    if len(clean) > 80:
        raise ValueError("เลขออเดอร์ยาวเกินไป")

    selected_carrier = (carrier or "auto").strip().lower()
    if selected_carrier not in {"auto", "skyfrog", "kex", "interexpress"}:
        raise ValueError("กรุณาเลือกผู้ให้บริการที่ถูกต้อง")

    def _choose(detected_carrier: str, number: str) -> tuple[str, str]:
        if selected_carrier not in {"auto", detected_carrier}:
            labels = {
                "skyfrog": "KLEAN&KARE",
                "kex": "KEX",
                "interexpress": "InterExpress",
            }
            raise ValueError(
                f"ข้อมูลระบุเป็น {labels[detected_carrier]} แต่เลือก {labels[selected_carrier]}"
            )
        return detected_carrier, number.upper()

    prefixed = KLEAN_ORDER_RE.fullmatch(clean.lstrip("#"))
    if prefixed:
        return _choose("skyfrog", prefixed.group(1))

    kex_prefixed = KEX_REFERENCE_RE.fullmatch(clean.lstrip("#"))
    if kex_prefixed:
        return _choose("kex", kex_prefixed.group(1))

    interexpress_prefixed = INTEREXPRESS_REFERENCE_RE.fullmatch(clean.lstrip("#"))
    if interexpress_prefixed:
        return _choose("interexpress", interexpress_prefixed.group(1))

    candidate = clean.lstrip("#").upper()
    if selected_carrier == "kex" and KEX_TRACKING_RE.fullmatch(candidate):
        return "kex", candidate
    if selected_carrier == "interexpress" and KEX_TRACKING_RE.fullmatch(candidate):
        return "interexpress", candidate
    if selected_carrier in {"auto", "skyfrog"} and ORDER_NUMBER_RE.fullmatch(candidate):
        return "skyfrog", candidate
    if selected_carrier == "auto" and KEX_TRACKING_RE.fullmatch(candidate):
        raise ValueError(
            "เลข ANB อาจเป็น KEX หรือ InterExpress กรุณาเลือกผู้ให้บริการก่อนค้นหา"
        )
    raise ValueError(
        "รูปแบบไม่ถูกต้อง กรุณาตรวจเลขและเลือกผู้ให้บริการให้ตรงกับเลขพัสดุ"
    )


def column_letter_to_index(value: str) -> int:
    letters = value.strip().upper()
    if not letters or not letters.isalpha():
        raise ValueError(f"คอลัมน์ไม่ถูกต้อง: {value!r}")
    result = 0
    for char in letters:
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def parse_order_date(order_number: str) -> date | None:
    token = order_number.strip()
    if len(token) < 6 or not token[:6].isdigit():
        return None
    try:
        return date(2000 + int(token[:2]), int(token[2:4]), int(token[4:6]))
    except ValueError:
        return None


def fetch_sheet_csv(url: str, *, timeout: float = 30) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "KleanPodChecker/1.0"},
    )
    response.raise_for_status()
    text = response.content.decode("utf-8-sig")
    if "<html" in text[:500].lower():
        raise RuntimeError(
            "Google Sheet ส่งกลับหน้าเว็บแทน CSV; กรุณาตรวจสิทธิ์แชร์หรือ SHEET_ID/SHEET_GID"
        )
    return text


def extract_order_refs(csv_text: str, *, tracking_column: str = "G") -> list[OrderRef]:
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return []

    headers = rows[0]
    column_index = _find_tracking_column(headers, tracking_column)
    orders: dict[tuple[str, str], OrderRef] = {}

    for row_number, row in enumerate(rows[1:], start=2):
        if column_index >= len(row):
            continue
        raw_value = row[column_index]
        for match in KLEAN_ORDER_RE.finditer(raw_value):
            order_number = match.group(1).upper()
            key = ("skyfrog", order_number)
            ref = orders.get(key)
            if ref is None:
                ref = OrderRef(
                    order_number=order_number,
                    order_date=parse_order_date(order_number),
                    carrier="skyfrog",
                )
                orders[key] = ref
            ref.merge(row_number, raw_value)
        for match in KEX_REFERENCE_RE.finditer(raw_value):
            tracking_number = match.group(1).upper()
            key = ("kex", tracking_number)
            ref = orders.get(key)
            if ref is None:
                ref = OrderRef(
                    order_number=tracking_number,
                    order_date=None,
                    carrier="kex",
                )
                orders[key] = ref
            ref.merge(row_number, raw_value)
        for match in INTEREXPRESS_REFERENCE_RE.finditer(raw_value):
            tracking_number = match.group(1).upper()
            key = ("interexpress", tracking_number)
            ref = orders.get(key)
            if ref is None:
                ref = OrderRef(
                    order_number=tracking_number,
                    order_date=None,
                    carrier="interexpress",
                )
                orders[key] = ref
            ref.merge(row_number, raw_value)

    return sorted(
        orders.values(),
        key=lambda item: (item.sheet_rows[0] if item.sheet_rows else 10**9, item.order_number),
    )


def _find_tracking_column(headers: list[str], fallback_column: str) -> int:
    normalized = [re.sub(r"\s+", " ", value).strip().casefold() for value in headers]
    for index, header in enumerate(normalized):
        if "tracking no" in header and "กรณีตามการจัดส่ง" in header:
            return index
    for index, header in enumerate(normalized):
        if "tracking no" in header:
            return index
    return column_letter_to_index(fallback_column)
