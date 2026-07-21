import unittest
from pathlib import Path

from klean_pod_checker.config import Settings
from klean_pod_checker.models import JobResult, OrderRef
from klean_pod_checker.sheets_sync import GoogleSheetsWriter, SheetSyncError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)

    def close(self):
        return None


def settings():
    return Settings(
        sheet_id="sheet-id",
        sheet_gid="976262542",
        sheet_tracking_column="G",
        skyfrog_customer_code="C000000",
        skyfrog_username="system",
        skyfrog_password="password",
        request_timeout_seconds=5,
        concurrency=1,
        request_delay_seconds=0,
        output_dir=Path("outputs"),
        state_db_path=Path("data/cache.sqlite"),
        cs_access_pin="12345678",
        web_secret_key="x" * 64,
        web_session_hours=12,
        google_sheets_webhook_url="https://script.google.test/macros/s/id/exec",
        google_sheets_webhook_secret="secret",
        public_base_url="https://ffm.example.test",
    )


class GoogleSheetsWriterTests(unittest.TestCase):
    def test_sends_only_requested_rows_and_columns_payload(self):
        session = FakeSession({"ok": True, "updated": 2})
        writer = GoogleSheetsWriter(settings(), session=session)
        ref = OrderRef(
            order_number="2607073BT72NEQ",
            order_date=None,
            carrier="kex",
            sheet_rows=[471, 472],
        )
        result = JobResult(
            order_number=ref.order_number,
            found=True,
            status_code="S",
            status_th="ออกนำส่ง",
            proof_urls=["https://www.skyfrog.net/store/C000000/pod/proof.jpg"],
            checked_at="2026-07-14T13:00:00+07:00",
        )

        self.assertEqual(writer.update_report_rows([(ref, result)]), 2)
        payload = session.calls[0][1]["json"]
        self.assertEqual([item["row"] for item in payload["updates"]], [471, 472])
        self.assertEqual(payload["updates"][0]["status"], "ออกนำส่ง (S)")
        self.assertEqual(len(payload["updates"][0]["proof_urls"]), 1)
        self.assertTrue(
            payload["updates"][0]["proof_urls"][0].startswith(
                "https://ffm.example.test/sheet-proof/"
            )
        )

    def test_raises_when_apps_script_rejects_update(self):
        writer = GoogleSheetsWriter(settings(), session=FakeSession({"ok": False, "error": "no"}))
        result = JobResult(order_number="2607073BT72NEQ", found=False)
        with self.assertRaises(SheetSyncError):
            writer.update_rows([471], result)

    def test_excludes_skyfrog_signature_images_before_sending_to_sheet(self):
        session = FakeSession({"ok": True, "updated": 1})
        writer = GoogleSheetsWriter(settings(), session=session)
        ref = OrderRef(
            order_number="2607073BT72NEQ",
            order_date=None,
            carrier="skyfrog",
            sheet_rows=[471],
        )
        result = JobResult(
            order_number=ref.order_number,
            found=True,
            proof_urls=[
                "https://www.skyfrog.net/store/C000000/pod/signature-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/signature-2.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-3.jpg",
            ],
        )

        writer.update_report_rows([(ref, result)])

        urls = session.calls[0][1]["json"]["updates"][0]["proof_urls"]
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://ffm.example.test/sheet-proof/"))

    def test_replaces_only_mapping_order_columns_through_webhook(self):
        session = FakeSession(
            {
                "ok": True,
                "action": "replace_mapping_order",
                "mapping_updated": 2,
            }
        )
        writer = GoogleSheetsWriter(settings(), session=session)

        updated = writer.replace_mapping_rows(
            [
                ("260706V7PN6E5H", "ANBL000008245"),
                ("260706V7PN6E5H", "ANBL000008245"),
                ("260706V7QKN53Q", "ANBL26F000006319"),
            ]
        )

        self.assertEqual(updated, 2)
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["action"], "replace_mapping_order")
        self.assertEqual(payload["sheet_name"], "Mapping Order")
        self.assertEqual(
            payload["rows"],
            [
                ("260706V7PN6E5H", "ANBL000008245"),
                ("260706V7QKN53Q", "ANBL26F000006319"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
