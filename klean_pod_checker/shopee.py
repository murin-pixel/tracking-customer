from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .sheets import INTEREXPRESS_REFERENCE_RE, KEX_REFERENCE_RE


SHOPEE_ORDER_RE = re.compile(r"[0-9]{6}[A-Z0-9]{4,}", re.IGNORECASE)
ANB_RE = re.compile(r"ANB[A-Z0-9]{6,}", re.IGNORECASE)
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class ShopeeTrackingRef:
    order_number: str
    tracking_number: str
    carrier: str


def parse_shopee_report(path: Path) -> list[ShopeeTrackingRef]:
    """Read only Shopee order and tracking columns from an exported XLSX file."""
    with zipfile.ZipFile(path) as workbook:
        shared_strings = _read_shared_strings(workbook)
        sheet_path = _first_sheet_path(workbook)
        rows = _read_rows(workbook, sheet_path, shared_strings)

    if not rows:
        return []
    header_row = rows[0]
    order_column = _find_column(header_row, "หมายเลขคำสั่งซื้อ")
    tracking_column = _find_column(header_row, "หมายเลขติดตามพัสดุ")
    if order_column is None or tracking_column is None:
        raise ValueError("ไม่พบคอลัมน์หมายเลขคำสั่งซื้อหรือหมายเลขติดตามพัสดุในรายงาน Shopee")

    references: dict[tuple[str, str], ShopeeTrackingRef] = {}
    for row in rows[1:]:
        order_number = _cell_value(row, order_column).upper()
        if not SHOPEE_ORDER_RE.fullmatch(order_number):
            continue
        extracted = extract_shopee_tracking(_cell_value(row, tracking_column))
        if extracted is None:
            continue
        carrier, tracking_number = extracted
        reference = ShopeeTrackingRef(order_number, tracking_number, carrier)
        references[(order_number, tracking_number)] = reference
    return sorted(references.values(), key=lambda item: (item.order_number, item.tracking_number))


def parse_shopee_mapping_rows(path: Path) -> list[tuple[str, str]]:
    """Read only Shopee order and raw tracking values from columns A and P."""
    with zipfile.ZipFile(path) as workbook:
        shared_strings = _read_shared_strings(workbook)
        sheet_path = _first_sheet_path(workbook)
        rows = _read_rows(workbook, sheet_path, shared_strings)

    if not rows:
        return []
    header_row = rows[0]
    order_column = _find_column(header_row, "หมายเลขคำสั่งซื้อ")
    tracking_column = _find_column(header_row, "หมายเลขติดตามพัสดุ")
    if order_column is None or tracking_column is None:
        raise ValueError("ไม่พบคอลัมน์หมายเลขคำสั่งซื้อหรือหมายเลขติดตามพัสดุในรายงาน Shopee")

    mappings: dict[tuple[str, str], None] = {}
    for row in rows[1:]:
        order_number = _cell_value(row, order_column).upper()
        tracking_number = _cell_value(row, tracking_column).strip()
        if not SHOPEE_ORDER_RE.fullmatch(order_number) or not tracking_number:
            continue
        mappings[(order_number, tracking_number)] = None
    return sorted(mappings, key=lambda item: (item[0], item[1]))


def parse_multiple_tracking_sheet(
    path: Path,
    *,
    sheet_name: str = "1 Order หลาย Tracking",
) -> list[ShopeeTrackingRef]:
    """Read grouped split-shipment references from the Google Sheet export.

    The sheet intentionally contains only an order label and a list of ANB
    numbers.  Its carrier is not recorded, so callers use the ``auto`` value
    and safely try KEX before InterExpress when checking the tracking number.
    """
    with zipfile.ZipFile(path) as workbook:
        shared_strings = _read_shared_strings(workbook)
        sheet_path = _sheet_path(workbook, sheet_name=sheet_name)
        rows = _read_rows(workbook, sheet_path, shared_strings)

    if not rows:
        return []
    header_row = rows[0]
    label_column = _find_column(header_row, "Extended Label")
    tracking_column = _find_column(header_row, "Tracking List")
    if label_column is None or tracking_column is None:
        raise ValueError("ไม่พบคอลัมน์ Extended Label หรือ Tracking List")

    references: dict[tuple[str, str], ShopeeTrackingRef] = {}
    for row in rows[1:]:
        order_match = SHOPEE_ORDER_RE.search(_cell_value(row, label_column))
        if not order_match:
            continue
        order_number = order_match.group(0).upper()
        for tracking_number in dict.fromkeys(
            ANB_RE.findall(_cell_value(row, tracking_column).upper())
        ):
            references[(order_number, tracking_number)] = ShopeeTrackingRef(
                order_number, tracking_number, "auto"
            )
    return sorted(references.values(), key=lambda item: (item.order_number, item.tracking_number))


def extract_shopee_tracking(value: str) -> tuple[str, str] | None:
    """Extract an ANB number and its carrier from Shopee column P values."""
    raw_value = value.strip()
    if not raw_value:
        return None
    interexpress = INTEREXPRESS_REFERENCE_RE.search(raw_value)
    if interexpress:
        return "interexpress", interexpress.group(1).upper()
    kex = KEX_REFERENCE_RE.search(raw_value)
    if kex:
        return "kex", kex.group(1).upper()

    tracking = ANB_RE.search(raw_value)
    if not tracking:
        return None
    carrier_name = raw_value.upper()
    if "INTEREXPRESS" in carrier_name or "INTER EXPRESS" in carrier_name:
        return "interexpress", tracking.group(0).upper()
    if "KEX" in carrier_name:
        return "kex", tracking.group(0).upper()
    return None


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall(f"{{{SPREADSHEET_NS}}}si"):
        values.append("".join(item.itertext()))
    return values


def _first_sheet_path(workbook: zipfile.ZipFile) -> str:
    return _sheet_path(workbook)


def _sheet_path(workbook: zipfile.ZipFile, *, sheet_name: str | None = None) -> str:
    workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
    sheets = workbook_root.findall(f"{{{SPREADSHEET_NS}}}sheets/{{{SPREADSHEET_NS}}}sheet")
    selected_sheet = next(
        (sheet for sheet in sheets if sheet.get("name") == sheet_name),
        None,
    ) if sheet_name else (sheets[0] if sheets else None)
    if selected_sheet is None:
        if sheet_name:
            raise ValueError(f"ไม่พบแท็บ {sheet_name}")
        raise ValueError("รายงาน Shopee ไม่มีชีตข้อมูล")
    relation_id = selected_sheet.get(f"{{{DOCUMENT_REL_NS}}}id")
    relationships = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    for relation in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        if relation.get("Id") == relation_id:
            target = relation.get("Target", "")
            normalized_target = target.lstrip("/")
            return (
                normalized_target
                if normalized_target.startswith("xl/")
                else f"xl/{normalized_target}"
            )
    raise ValueError("ไม่พบชีตข้อมูลในรายงาน Shopee")


def _read_rows(
    workbook: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[dict[int, str]]:
    root = ElementTree.fromstring(workbook.read(sheet_path))
    rows: list[dict[int, str]] = []
    for row in root.findall(f".//{{{SPREADSHEET_NS}}}sheetData/{{{SPREADSHEET_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{SPREADSHEET_NS}}}c"):
            reference = cell.get("r", "")
            column = _column_number(reference)
            if column is None:
                continue
            values[column] = _cell_text(cell, shared_strings)
        rows.append(values)
    return rows


def _cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{SPREADSHEET_NS}}}is")
        return "".join(inline.itertext()) if inline is not None else ""
    value = cell.findtext(f"{{{SPREADSHEET_NS}}}v", default="")
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return ""
    return value


def _column_number(reference: str) -> int | None:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    if not letters:
        return None
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - ord("A") + 1
    return number - 1


def _find_column(row: dict[int, str], needle: str) -> int | None:
    normalized_needle = needle.replace("*", "").replace(" ", "").casefold()
    for column, value in row.items():
        normalized_value = value.replace("*", "").replace(" ", "").casefold()
        if normalized_needle in normalized_value:
            return column
    return None


def _cell_value(row: dict[int, str], column: int) -> str:
    return str(row.get(column, "")).strip()
