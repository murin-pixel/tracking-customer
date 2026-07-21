import tempfile
import unittest
import zipfile
from pathlib import Path

from klean_pod_checker.shopee import (
    extract_shopee_tracking,
    parse_multiple_tracking_sheet,
    parse_shopee_mapping_rows,
    parse_shopee_report,
)


class ShopeeReportTests(unittest.TestCase):
    def test_extracts_kex_and_interexpress_values(self):
        self.assertEqual(
            extract_shopee_tracking("KEX_เลขพัสดุ_ANBL000008245"),
            ("kex", "ANBL000008245"),
        )
        self.assertEqual(
            extract_shopee_tracking("INTEREXPRESS *เลขพัสดุ* ANBL26F000006319"),
            ("interexpress", "ANBL26F000006319"),
        )
        self.assertIsNone(extract_shopee_tracking("จัดส่งโดย Shopee Xpress"))

    def test_reads_only_order_and_tracking_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Order.all.xlsx"
            _write_minimal_report(path)
            result = parse_shopee_report(path)
        self.assertEqual(
            result,
            [
                _ref("260706V7PN6E5H", "ANBL000008245", "kex"),
                _ref("260706V7QKN53Q", "ANBL26F000006319", "interexpress"),
            ],
        )

    def test_reads_every_tracking_number_from_grouped_tracking_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracking-groups.xlsx"
            _write_multiple_tracking_sheet(path)
            result = parse_multiple_tracking_sheet(path)
        self.assertEqual(
            result,
            [
                _ref("260706V7PN6E5H", "ANBL000008245", "auto"),
                _ref("260706V7PN6E5H", "ANBL26F000006319", "auto"),
            ],
        )

    def test_reads_raw_columns_a_and_p_for_mapping_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Order.all.xlsx"
            _write_minimal_report(path)
            result = parse_shopee_mapping_rows(path)
        self.assertEqual(
            result,
            [
                ("260706V7PN6E5H", "KEX_เลขพัสดุ_ANBL000008245"),
                ("260706V7QKN53Q", "INTEREXPRESS *เลขพัสดุ* ANBL26F000006319"),
            ],
        )


def _ref(order_number, tracking_number, carrier):
    from klean_pod_checker.shopee import ShopeeTrackingRef

    return ShopeeTrackingRef(order_number, tracking_number, carrier)


def _write_minimal_report(path: Path):
    workbook_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">
      <sheets><sheet name=\"orders\" sheetId=\"1\" r:id=\"rId1\"/></sheets>
    </workbook>"""
    relationships_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
      <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>
    </Relationships>"""
    shared_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"6\" uniqueCount=\"6\">
      <si><t>หมายเลขคำสั่งซื้อ</t></si><si><t>*หมายเลขติดตามพัสดุ</t></si>
      <si><t>260706V7PN6E5H</t></si><si><t>KEX_เลขพัสดุ_ANBL000008245</t></si>
      <si><t>260706V7QKN53Q</t></si><si><t>INTEREXPRESS *เลขพัสดุ* ANBL26F000006319</t></si>
    </sst>"""
    sheet_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>
      <row r=\"1\"><c r=\"A1\" t=\"s\"><v>0</v></c><c r=\"P1\" t=\"s\"><v>1</v></c></row>
      <row r=\"2\"><c r=\"A2\" t=\"s\"><v>2</v></c><c r=\"P2\" t=\"s\"><v>3</v></c></row>
      <row r=\"3\"><c r=\"A3\" t=\"s\"><v>4</v></c><c r=\"P3\" t=\"s\"><v>5</v></c></row>
    </sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _write_multiple_tracking_sheet(path: Path):
    workbook_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">
      <sheets><sheet name=\"1 Order หลาย Tracking\" sheetId=\"1\" r:id=\"rId1\"/></sheets>
    </workbook>"""
    relationships_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
      <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>
    </Relationships>"""
    shared_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"6\" uniqueCount=\"6\">
      <si><t>Extended Label</t></si><si><t>Tracking Count</t></si><si><t>Tracking List</t></si>
      <si><t>260706V7PN6E5H</t></si><si><t>2</t></si><si><t>ANBL000008245, ANBL26F000006319</t></si>
    </sst>"""
    sheet_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>
      <row r=\"1\"><c r=\"A1\" t=\"s\"><v>0</v></c><c r=\"B1\" t=\"s\"><v>1</v></c><c r=\"C1\" t=\"s\"><v>2</v></c></row>
      <row r=\"2\"><c r=\"A2\" t=\"s\"><v>3</v></c><c r=\"B2\" t=\"s\"><v>4</v></c><c r=\"C2\" t=\"s\"><v>5</v></c></row>
    </sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
