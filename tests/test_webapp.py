import tempfile
import unittest
from pathlib import Path

from klean_pod_checker.config import Settings
from klean_pod_checker.models import JobResult
from klean_pod_checker.shopee import ShopeeTrackingRef
from klean_pod_checker.storage import StatusCache
from klean_pod_checker.webapp import _customer_stage, create_app


class FakeSearchService:
    def __init__(self):
        self.orders = []

    def search(self, order_number, carrier="skyfrog"):
        self.orders.append((carrier, order_number))
        return JobResult(
            order_number=order_number,
            found=True,
            status_code="C",
            status_en="Completed",
            status_th="จัดส่งสำเร็จ",
            delivered=True,
            driver="Driver One",
            customer="Sensitive Customer Name",
            created_at="2026-07-13T09:00:00+07:00",
            delivery_at="2026-07-14T11:45:00+07:00",
            updated_at="2026-07-14T11:45:00+07:00",
            proof_urls=[
                "https://www.skyfrog.net/store/C000000/pod/signature-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/signature-2.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-3.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-4.jpg",
            ],
            checked_at="2026-07-14T12:00:00+07:00",
            raw={"secret_internal_field": "must-not-leak"},
        )


class FakeSheetWriter:
    enabled = True

    def __init__(self):
        self.updates = []

    def update_rows(self, rows, result, *, carrier=""):
        self.updates.append((list(rows), result.order_number, carrier))
        return len(list(rows))


class FallbackSearchService:
    def __init__(self):
        self.orders = []

    def search(self, order_number, carrier="skyfrog"):
        self.orders.append((carrier, order_number))
        return JobResult(
            order_number=order_number,
            found=carrier == "interexpress",
            group_name="InterExpress" if carrier == "interexpress" else "KEX",
            status_th="จัดส่งสำเร็จ" if carrier == "interexpress" else "",
            delivered=carrier == "interexpress",
        )


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        output_dir = root / "outputs"
        output_dir.mkdir()
        (output_dir / "latest.html").write_text("<h1>report</h1>", encoding="utf-8")
        (output_dir / "latest.csv").write_text(
            "sheet_rows,order_number,found,delivered\n"
            "12,260608G0EEY23B,True,True\n"
            "13,B,False,False\n",
            encoding="utf-8",
        )
        self.settings = Settings(
            sheet_id="sheet",
            sheet_gid="1",
            sheet_tracking_column="G",
            skyfrog_customer_code="C000000",
            skyfrog_username="system",
            skyfrog_password="password",
            request_timeout_seconds=1,
            concurrency=1,
            request_delay_seconds=0,
            output_dir=output_dir,
            state_db_path=root / "data" / "cache.sqlite",
            cs_access_pin="12345678",
            web_secret_key="x" * 64,
            web_session_hours=12,
            google_sheets_webhook_url="https://example.test/webhook",
            google_sheets_webhook_secret="sync-secret",
            kex_proof_pin="0000",
            kex_proof_dir=root / "data" / "kex-proofs",
            public_base_url="https://ffm.example.test",
        )
        self.search = FakeSearchService()
        self.writer = FakeSheetWriter()
        self.multiple_tracking_sync_calls = 0

        def sync_multiple_tracking():
            self.multiple_tracking_sync_calls += 1
            return 0

        self.app = create_app(
            settings=self.settings,
            search_service=self.search,
            sheet_writer=self.writer,
            multiple_tracking_sync=sync_multiple_tracking,
        )
        self.app.testing = True
        self.client = self.app.test_client()
        self.base_url = "https://localhost"

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self):
        return self.client.post(
            "/login",
            data={"pin": "12345678"},
            base_url=self.base_url,
        )

    def test_requires_login(self):
        response = self.client.post(
            "/api/check",
            json={"order": "260608G0EEY23B"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.get("/report", base_url=self.base_url).status_code,
            302,
        )

    def test_customer_page_and_lookup_are_public_and_sanitized(self):
        page = self.client.get("/customer.html", base_url=self.base_url)
        self.assertEqual(page.status_code, 200)
        self.assertIn("ตรวจสอบสถานะขนส่ง".encode(), page.data)
        self.assertIn(b"bedee-logo.png", page.data)

        response = self.client.post(
            "/api/customer-check",
            json={"order": "260608G0EEY23B", "carrier": "auto"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["order_number"], "260608G0EEY23B")
        self.assertEqual(result["carrier"], "KLEAN&KARE")
        self.assertEqual(result["stage"], "delivered")
        self.assertFalse(result["exception"])
        self.assertEqual(result["created_at"], "2026-07-13T09:00:00+07:00")
        self.assertNotIn("customer", result)
        self.assertNotIn("driver", result)
        self.assertNotIn("proof_urls", result)
        self.assertNotIn("Sensitive Customer Name", response.get_data(as_text=True))

    def test_customer_delivery_in_progress_maps_to_out_for_delivery(self):
        result = JobResult(
            order_number="ANBL26F000006103",
            found=True,
            status_th="พัสดุอยู่ระหว่างการนำส่ง",
        )

        self.assertEqual(_customer_stage(result), ("out_for_delivery", False))

    def test_customer_kex_delivering_maps_to_out_for_delivery(self):
        result = JobResult(
            order_number="ANBL000012340",
            found=True,
            status_code="045",
            status_th="กำลังจัดส่งพัสดุ",
        )

        self.assertEqual(_customer_stage(result), ("out_for_delivery", False))

    def test_customer_anb_falls_back_from_kex_to_interexpress(self):
        fallback = FallbackSearchService()
        app = create_app(
            settings=self.settings,
            search_service=fallback,
            sheet_writer=FakeSheetWriter(),
            multiple_tracking_sync=lambda: 0,
        )
        response = app.test_client().post(
            "/api/customer-check", json={"order": "ANBL26F000006103"}, base_url=self.base_url
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            fallback.orders,
            [("kex", "ANBL26F000006103"), ("interexpress", "ANBL26F000006103")],
        )
        result = response.get_json()["result"]
        self.assertEqual(result["carrier"], "InterExpress")
        self.assertEqual(result["stage"], "delivered")

    def test_customer_shopee_order_uses_imported_tracking_number(self):
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_tracking_refs(
                [ShopeeTrackingRef("260706V7PN6E5H", "ANBL000008245", "kex")]
            )
        finally:
            cache.close()

        response = self.client.post(
            "/api/customer-check", json={"order": "260706V7PN6E5H"}, base_url=self.base_url
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.search.orders, [("kex", "ANBL000008245")])
        result = response.get_json()["result"]
        self.assertEqual(result["lookup_order"], "260706V7PN6E5H")
        self.assertEqual(result["order_number"], "ANBL000008245")
        self.assertEqual(result["carrier"], "KEX")

    def test_cs_search_groups_multiple_tracking_numbers_from_order_or_track(self):
        order_number = "260706V7PN6E5H"
        first_tracking = "ANBL000008245"
        second_tracking = "ANBL26F000006319"
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_tracking_refs(
                [
                    ShopeeTrackingRef(order_number, first_tracking, "kex"),
                    ShopeeTrackingRef(order_number, second_tracking, "interexpress"),
                ]
            )
        finally:
            cache.close()
        self.login()

        response = self.client.post(
            "/api/check",
            json={"order": first_tracking, "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["group"]["order_number"], order_number)
        self.assertTrue(payload["group"]["multiple"])
        self.assertEqual(payload["group"]["total"], 2)
        self.assertEqual(
            [entry["tracking_number"] for entry in payload["results"]],
            [first_tracking, second_tracking],
        )
        self.assertEqual(
            self.search.orders,
            [("kex", first_tracking), ("interexpress", second_tracking)],
        )
        self.assertEqual(self.multiple_tracking_sync_calls, 1)

    def test_cs_search_uses_mapping_order_for_order_number(self):
        order_number = "260706V7PN6E5H"
        first_tracking = "ANBL000008245"
        second_tracking = "ANBL26F000006319"
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_mapping_rows(
                [
                    (order_number, f"KEX_เลขพัสดุ_{first_tracking}"),
                    (order_number, f"INTEREXPRESS เลขพัสดุ {second_tracking}"),
                ]
            )
        finally:
            cache.close()
        self.login()

        response = self.client.post(
            "/api/check",
            json={"order": order_number, "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["group"]["multiple"])
        self.assertEqual(payload["group"]["total"], 2)
        self.assertEqual(
            [entry["tracking_number"] for entry in payload["results"]],
            [first_tracking, second_tracking],
        )
        self.assertEqual(
            self.search.orders,
            [("kex", first_tracking), ("interexpress", second_tracking)],
        )

    def test_cs_search_uses_klean_mapping_order_value(self):
        order_number = "260706V7PN6E5H"
        skyfrog_order = "260706V7WGGUCD"
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_mapping_rows(
                [(order_number, f"จัดส่งโดย_KLEAN&KARE_{skyfrog_order}")]
            )
        finally:
            cache.close()
        self.login()

        response = self.client.post(
            "/api/check",
            json={"order": order_number, "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.search.orders, [("skyfrog", skyfrog_order)])

    def test_cs_tracking_search_displays_mapping_order_number(self):
        order_number = "260706V7PN6E5H"
        tracking_number = "ANBL000002152"
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_mapping_rows(
                [(order_number, f"KEX_เลขพัสดุ_{tracking_number}")]
            )
        finally:
            cache.close()
        self.login()

        response = self.client.post(
            "/api/check",
            json={"order": tracking_number, "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["order_number"], order_number)
        self.assertEqual(result["tracking_number"], tracking_number)
        self.assertEqual(result["carrier"], "KEX")
        self.assertEqual(self.search.orders, [("kex", tracking_number)])

    def test_cs_order_search_displays_tracking_and_carrier(self):
        order_number = "260706V7PN6E5H"
        tracking_number = "ANBL000002152"
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_mapping_rows(
                [(order_number, f"KEX_เลขพัสดุ_{tracking_number}")]
            )
        finally:
            cache.close()
        self.login()

        response = self.client.post(
            "/api/check",
            json={"order": order_number, "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["order_number"], tracking_number)
        self.assertEqual(result["tracking_number"], tracking_number)
        self.assertEqual(result["mapping_order_number"], order_number)
        self.assertEqual(result["carrier"], "KEX")

    def test_rejects_wrong_pin_and_accepts_correct_pin(self):
        wrong = self.client.post(
            "/login",
            data={"pin": "00000000"},
            base_url=self.base_url,
        )
        self.assertEqual(wrong.status_code, 200)
        self.assertIn("PIN ไม่ถูกต้อง".encode(), wrong.data)
        correct = self.login()
        self.assertEqual(correct.status_code, 302)
        dashboard = self.client.get("/", base_url=self.base_url)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("ภาพรวมจาก Google Sheet".encode(), dashboard.data)

    def test_live_search_normalizes_and_hides_raw_data(self):
        self.login()
        response = self.client.post(
            "/api/check",
            json={"order": "KLEAN&KARE_260608g0eey23b"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["order_number"], "260608G0EEY23B")
        self.assertTrue(result["delivered"])
        self.assertNotIn("raw", result)
        self.assertNotIn("customer", result)
        self.assertNotIn(b"Sensitive Customer Name", response.data)
        self.assertEqual(
            result["proof_urls"],
            [
                "https://www.skyfrog.net/store/C000000/pod/photo-3.jpg",
                "https://www.skyfrog.net/store/C000000/pod/photo-4.jpg",
            ],
        )
        self.assertTrue(result["proof_urls_filtered"])
        self.assertEqual(self.search.orders, [("skyfrog", "260608G0EEY23B")])
        self.assertEqual(response.get_json()["sheet_sync"]["updated_rows"], 1)
        self.assertEqual(self.writer.updates, [([12], "260608G0EEY23B", "skyfrog")])

    def test_protected_report_and_download(self):
        self.login()
        report = self.client.get("/report", base_url=self.base_url)
        download = self.client.get("/download/latest.csv", base_url=self.base_url)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])

    def test_kex_search_and_proof_are_protected(self):
        self.login()
        response = self.client.post(
            "/api/check",
            json={"order": "KEX_เลขพัสดุ_ANBL000005925", "carrier": "kex"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.search.orders[-1], ("kex", "ANBL000005925"))

        self.client.post("/logout", base_url=self.base_url)
        protected = self.client.get(
            "/proof/kex/ANBL000005925/proof-1-1234567890.jpg",
            base_url=self.base_url,
        )
        self.assertEqual(protected.status_code, 302)

    def test_interexpress_search_uses_prefixed_input(self):
        self.login()
        response = self.client.post(
            "/api/check",
            json={
                "order": "INTEREXPRESS *เลขพัสดุ* ANBL26F000006319",
                "carrier": "interexpress",
            },
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.search.orders[-1], ("interexpress", "ANBL26F000006319")
        )

    def test_bare_anb_search_can_still_use_selected_carrier(self):
        self.login()
        response = self.client.post(
            "/api/check",
            json={"order": "ANBL26F000006103", "carrier": "interexpress"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.search.orders[-1], ("interexpress", "ANBL26F000006103")
        )

        automatic = self.client.post(
            "/api/check",
            json={"order": "ANBL26F000006103", "carrier": "auto"},
            base_url=self.base_url,
        )
        self.assertEqual(automatic.status_code, 200)
        self.assertEqual(self.search.orders[-1], ("skyfrog", "ANBL26F000006103"))

    def test_automatic_search_uses_carrier_priority(self):
        fallback = FallbackSearchService()
        app = create_app(
            settings=self.settings,
            search_service=fallback,
            sheet_writer=FakeSheetWriter(),
            multiple_tracking_sync=lambda: 0,
        )
        client = app.test_client()
        client.post("/login", data={"pin": "12345678"}, base_url=self.base_url)

        response = client.post(
            "/api/check",
            json={"order": "KEX_เลขพัสดุ_ANBL26F000006103", "carrier": "auto"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            fallback.orders,
            [
                ("skyfrog", "ANBL26F000006103"),
                ("kex", "ANBL26F000006103"),
                ("interexpress", "ANBL26F000006103"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
