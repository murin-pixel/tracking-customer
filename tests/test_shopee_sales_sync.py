from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from klean_pod_checker.shopee_sales_sync import (
    newest_report,
    reports_from_manifest,
    retained_mapping_rows,
)


class ShopeeSalesSyncTests(unittest.TestCase):
    def test_mapping_sheet_keeps_45_days_and_excludes_klean(self):
        rows = [
            ("260602OLDTRACK", "KEX_เลขพัสดุ_ANBL000000001"),
            ("260603KEEPKEX", "KEX_เลขพัสดุ_ANBL000000002"),
            ("260604KEEPINT", "INTEREXPRESS เลขพัสดุ ANBL26F000000003"),
            ("260705KLEANMAP", "จัดส่งโดย_KLEAN&KARE_260705KLEANJOB"),
            ("260706UNKNOWN1", "ไม่ทราบขนส่ง"),
        ]

        retained = retained_mapping_rows(rows, cutoff=date(2026, 6, 3))

        self.assertEqual(
            retained,
            [
                ("260603KEEPKEX", "KEX_เลขพัสดุ_ANBL000000002"),
                ("260604KEEPINT", "INTEREXPRESS เลขพัสดุ ANBL26F000000003"),
            ],
        )

    def test_newest_report_returns_latest_non_empty_matching_file(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            older = directory / "acc1-Order.all.20260701_20260715.xlsx"
            newer = directory / "acc1-Order.all.20260701_20260716.xlsx"
            ignored = directory / "acc2-Order.all.20260701_20260716.xlsx"
            older.write_bytes(b"x" * 1_001)
            newer.write_bytes(b"y" * 1_001)
            ignored.write_bytes(b"z" * 1_001)
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))

            self.assertEqual(
                newest_report(directory, "acc1-Order.all.*.xlsx"),
                newer,
            )

    def test_newest_report_rejects_missing_or_empty_reports(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            (directory / "acc1-Order.all.20260701_20260716.xlsx").write_bytes(b"")

            with self.assertRaises(FileNotFoundError):
                newest_report(directory, "acc1-Order.all.*.xlsx")

    def test_manifest_returns_all_latest_report_parts_only(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            first = directory / "Order.all.123.part-1.xlsx"
            second = directory / "Order.all.123.part-2.xlsx"
            ignored = directory.parent / "outside.xlsx"
            first.write_bytes(b"x" * 1_001)
            second.write_bytes(b"y" * 1_001)
            ignored.write_bytes(b"z" * 1_001)
            manifest = directory / "latest-report-manifest.json"
            manifest.write_text(
                '{"reports": ["%s", "%s", "%s"]}'
                % (first, second, ignored),
                encoding="utf-8",
            )

            self.assertEqual(
                reports_from_manifest(manifest, directory),
                (first.resolve(), second.resolve()),
            )
