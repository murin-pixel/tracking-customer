import unittest

from klean_pod_checker.models import JobResult
from klean_pod_checker.supabase_status_cache import SupabaseStatusCache


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = [] if payload is None else payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0) if self.responses else FakeResponse()

    def close(self):
        return None


class SupabaseStatusCacheTests(unittest.TestCase):
    def test_disabled_cache_returns_no_result(self):
        cache = SupabaseStatusCache("", "")

        self.assertFalse(cache.enabled)
        self.assertIsNone(cache.get("ANBL000012448"))

    def test_put_excludes_private_carrier_fields(self):
        session = FakeSession()
        cache = SupabaseStatusCache(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )
        result = JobResult(
            order_number="ANBL000012448",
            found=True,
            status_code="110",
            status_th="พัสดุออกจากศูนย์กระจายสินค้า",
            group_name="KEX",
            location="บ้านไผ่, ขอนแก่น",
            checked_at="2026-07-23T13:00:00+07:00",
            customer="Sensitive Customer",
            driver="Sensitive Driver",
            proof_urls=["https://private.example/proof.jpg"],
            raw={"address": "Sensitive Address", "product": "Sensitive Product"},
        )

        cache.put(result)

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url, "https://project.supabase.co/rest/v1/tracking_status_cache"
        )
        body = kwargs["json"]
        self.assertEqual(body["tracking_number"], "ANBL000012448")
        self.assertEqual(body["payload"]["location"], "บ้านไผ่, ขอนแก่น")
        self.assertNotIn("customer", body["payload"])
        self.assertNotIn("driver", body["payload"])
        self.assertNotIn("proof_urls", body["payload"])
        self.assertNotIn("raw", body["payload"])

    def test_reads_cached_result_and_can_filter_final_status(self):
        payload = {
            "order_number": "ANBL000012448",
            "found": True,
            "status_code": "C",
            "status_th": "พัสดุจัดส่งสำเร็จ",
            "delivered": True,
            "group_name": "KEX",
            "checked_at": "2026-07-23T13:00:00+07:00",
        }
        session = FakeSession([FakeResponse([{"payload": payload}])])
        cache = SupabaseStatusCache(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )

        result = cache.get_final("anbl000012448")

        self.assertIsNotNone(result)
        self.assertTrue(result.delivered)
        params = session.calls[0][2]["params"]
        self.assertEqual(params["tracking_number"], "eq.ANBL000012448")
        self.assertEqual(params["is_final"], "eq.true")


if __name__ == "__main__":
    unittest.main()
