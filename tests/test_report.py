import tempfile
import unittest
from datetime import date
from pathlib import Path

from klean_pod_checker.models import JobResult, OrderRef
from klean_pod_checker.report import write_reports


class ReportTests(unittest.TestCase):
    def test_html_report_renders_clickable_proof_thumbnails(self):
        ref = OrderRef(
            order_number="2607073BT72NEQ",
            order_date=date(2026, 7, 7),
            sheet_rows=[471],
            carrier="kex",
        )
        result = JobResult(
            order_number=ref.order_number,
            found=True,
            status_code="S",
            status_th="ออกนำส่ง",
            customer="Sensitive Customer Name",
            proof_urls=[
                "https://www.skyfrog.net/store/C000000/pod/proof-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/proof-2.jpg",
            ],
            checked_at="2026-07-14T12:32:55+07:00",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports([(ref, result)], Path(directory), keep_history=False)
            report = paths["latest_html"].read_text(encoding="utf-8")
            csv_report = paths["latest_csv"].read_text(encoding="utf-8-sig")

        self.assertEqual(report.count('class="proof-thumb"'), 2)
        self.assertEqual(report.count('loading="lazy"'), 2)
        self.assertIn('alt="รูปหลักฐาน 1"', report)
        self.assertIn('target="_blank"', report)
        self.assertIn('id="report-search"', report)
        self.assertIn('data-filter="actionable"', report)
        self.assertIn('data-carrier="skyfrog"', report)
        self.assertIn('data-row-carrier="kex"', report)
        self.assertIn('data-carrier="interexpress"', report)
        self.assertIn('id="export-visible"', report)
        self.assertIn('data-carrier="kex"', report)
        self.assertIn('data-action="refresh"', report)
        self.assertIn('/static/report.js', report)
        self.assertIn('id="order-dialog"', report)
        self.assertNotIn("Sensitive Customer Name", report)
        self.assertNotIn("<th>ลูกค้า</th>", report)
        self.assertNotIn("customer", csv_report.splitlines()[0])
        self.assertNotIn("Sensitive Customer Name", csv_report)

    def test_html_report_hides_skyfrog_signature_images(self):
        ref = OrderRef(
            order_number="2607073BT72NEQ",
            order_date=date(2026, 7, 7),
            sheet_rows=[471],
            carrier="skyfrog",
        )
        result = JobResult(
            order_number=ref.order_number,
            found=True,
            status_code="A",
            status_th="จัดส่งสำเร็จ",
            proof_urls=[
                "https://www.skyfrog.net/store/C000000/pod/signature-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/signature-2.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-3.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-4.jpg",
            ],
            checked_at="2026-07-16T10:00:00+07:00",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports([(ref, result)], Path(directory), keep_history=False)
            report = paths["latest_html"].read_text(encoding="utf-8")
            csv_report = paths["latest_csv"].read_text(encoding="utf-8-sig")

        self.assertEqual(report.count('class="proof-thumb"'), 2)
        self.assertIn('alt="รูปหลักฐาน 3"', report)
        self.assertIn("photo-3.jpg", report)
        self.assertNotIn("signature-1.jpg", report)
        self.assertNotIn("signature-2.jpg", report)
        self.assertNotIn("signature-1.jpg", csv_report)
        self.assertNotIn("signature-2.jpg", csv_report)


if __name__ == "__main__":
    unittest.main()
