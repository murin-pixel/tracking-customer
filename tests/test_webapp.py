import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import requests

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
            proof_urls=["https://example.test/private-proof.jpg"],
            checked_at="2026-07-14T12:00:00+07:00",
            raw={"secret_internal_field": "must-not-leak"},
        )


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


class UnavailableSearchService:
    def search(self, order_number, carrier="skyfrog"):
        raise requests.ReadTimeout("carrier timeout")


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
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
            output_dir=root / "outputs",
            state_db_path=root / "data" / "cache.sqlite",
            web_secret_key="x" * 64,
            google_sheets_webhook_url="https://example.test/webhook",
            google_sheets_webhook_secret="sync-secret",
            kex_proof_pin="0000",
            kex_proof_dir=root / "data" / "kex-proofs",
            public_base_url="https://ffm.example.test",
        )
        self.search = FakeSearchService()
        self.app = create_app(settings=self.settings, search_service=self.search)
        self.app.testing = True
        self.client = self.app.test_client()
        self.base_url = "https://localhost"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_root_and_customer_url_show_customer_tracking_only(self):
        root = self.client.get("/", base_url=self.base_url)
        customer = self.client.get("/customer.html", base_url=self.base_url)

        self.assertEqual(root.status_code, 200)
        self.assertEqual(customer.status_code, 200)
        self.assertIn("ตรวจสอบสถานะขนส่ง".encode(), root.data)
        self.assertIn(b"bedee-logo.png", root.data)
        self.assertNotIn(b"login", root.data.lower())

    def test_legacy_cs_routes_are_not_available(self):
        requests = (
            ("get", "/login"),
            ("post", "/login"),
            ("post", "/logout"),
            ("post", "/api/check"),
            ("get", "/report"),
            ("get", "/latest.html"),
            ("get", "/download/latest.csv"),
            ("get", "/proof/kex/ANBL000005925/proof-1-1234567890.jpg"),
        )
        for method, path in requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, base_url=self.base_url)
                self.assertEqual(response.status_code, 404)

    def test_customer_lookup_is_public_and_sanitized(self):
        response = self.client.post(
            "/api/customer-check",
            json={"order": "260608G0EEY23B"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["order_number"], "260608G0EEY23B")
        self.assertEqual(result["carrier"], "KLEAN&KARE")
        self.assertEqual(result["stage"], "delivered")
        self.assertFalse(result["exception"])
        self.assertEqual(result["created_at"], "2026-07-13T09:00:00+07:00")
        for private_field in ("customer", "driver", "proof_urls", "raw"):
            self.assertNotIn(private_field, result)
        self.assertNotIn("Sensitive Customer Name", response.get_data(as_text=True))
        self.assertNotIn("must-not-leak", response.get_data(as_text=True))

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

    def test_customer_kex_hub_statuses_map_to_in_transit(self):
        statuses = (
            "พัสดุออกจากศูนย์กระจายสินค้า",
            "พัสดุถึงคลังสินค้าปลายทาง",
        )
        for status in statuses:
            with self.subTest(status=status):
                result = JobResult(
                    order_number="ANBL000012340",
                    found=True,
                    status_th=status,
                )
                self.assertEqual(_customer_stage(result), ("in_transit", False))

    def test_customer_anb_falls_back_from_kex_to_interexpress(self):
        fallback = FallbackSearchService()
        app = create_app(settings=self.settings, search_service=fallback)
        response = app.test_client().post(
            "/api/customer-check",
            json={"order": "ANBL26F000006103"},
            base_url=self.base_url,
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
            "/api/customer-check",
            json={"order": "260706V7PN6E5H"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.search.orders, [("kex", "ANBL000008245")])
        result = response.get_json()["result"]
        self.assertEqual(result["lookup_order"], "260706V7PN6E5H")
        self.assertEqual(result["order_number"], "ANBL000008245")
        self.assertEqual(result["carrier"], "KEX")

    def test_customer_shopee_order_uses_imported_mapping_fallback(self):
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put_shopee_mapping_rows(
                [("260706V7PN6E5H", "INTEREXPRESS เลขพัสดุ ANBL26F000006319")]
            )
        finally:
            cache.close()

        response = self.client.post(
            "/api/customer-check",
            json={"order": "260706V7PN6E5H"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.search.orders, [("interexpress", "ANBL26F000006319")])
        result = response.get_json()["result"]
        self.assertEqual(result["lookup_order"], "260706V7PN6E5H")
        self.assertEqual(result["carrier"], "InterExpress")

    def test_customer_uses_recent_cache_when_carrier_times_out(self):
        cached_result = JobResult(
            order_number="ANBL000012448",
            found=True,
            status_code="110",
            status_th="พัสดุออกจากศูนย์กระจายสินค้า",
            group_name="KEX",
            checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            customer="Sensitive Customer Name",
            raw={"private": "must-not-leak"},
        )
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put(cached_result)
        finally:
            cache.close()
        app = create_app(
            settings=self.settings,
            search_service=UnavailableSearchService(),
        )

        response = app.test_client().post(
            "/api/customer-check",
            json={"order": "ANBL000012448"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertTrue(result["cached"])
        self.assertEqual(result["stage"], "in_transit")
        self.assertEqual(result["carrier"], "KEX")
        self.assertNotIn("customer", result)
        self.assertNotIn("raw", result)

    def test_customer_klean_order_uses_recent_cache_when_skyfrog_times_out(self):
        cached_result = JobResult(
            order_number="2607218HNJ7240",
            found=True,
            status_code="R",
            status_th="กำลังขนส่ง",
            group_name="KLEAN&KARE",
            checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put(cached_result)
        finally:
            cache.close()
        app = create_app(
            settings=self.settings,
            search_service=UnavailableSearchService(),
        )

        response = app.test_client().post(
            "/api/customer-check",
            json={"order": "2607218HNJ7240"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertTrue(result["cached"])
        self.assertEqual(result["carrier"], "KLEAN&KARE")
        self.assertEqual(result["lookup_order"], "2607218HNJ7240")

    def test_customer_rejects_stale_cache_when_carrier_times_out(self):
        cached_result = JobResult(
            order_number="ANBL000012448",
            found=True,
            status_th="รับงานแล้ว",
            group_name="KEX",
            checked_at=(datetime.now().astimezone() - timedelta(days=2)).isoformat(
                timespec="seconds"
            ),
        )
        cache = StatusCache(self.settings.state_db_path)
        try:
            cache.put(cached_result)
        finally:
            cache.close()
        app = create_app(
            settings=self.settings,
            search_service=UnavailableSearchService(),
        )

        response = app.test_client().post(
            "/api/customer-check",
            json={"order": "ANBL000012448"},
            base_url=self.base_url,
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json()["error"],
            "ยังเชื่อมต่อระบบขนส่งไม่ได้ กรุณาลองใหม่อีกครั้ง",
        )

    def test_invalid_customer_input_returns_json_error(self):
        response = self.client.post(
            "/api/customer-check",
            json={"order": "not-a-tracking-number"},
            base_url=self.base_url,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_security_headers_apply_to_customer_page(self):
        response = self.client.get("/", base_url=self.base_url)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
