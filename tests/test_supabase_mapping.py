import unittest

from klean_pod_checker.shopee import ShopeeTrackingRef
from klean_pod_checker.supabase_mapping import SupabaseMappingStore


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


class SupabaseMappingStoreTests(unittest.TestCase):
    def test_disabled_store_returns_no_mapping(self):
        store = SupabaseMappingStore("", "")
        self.assertFalse(store.enabled)
        self.assertEqual(store.get_tracking_refs("260706V7PN6E5H"), [])

    def test_reads_normalized_tracking_references(self):
        session = FakeSession(
            [FakeResponse([{"tracking_number": "anbl000008245", "carrier": "KEX"}])]
        )
        store = SupabaseMappingStore(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )

        references = store.get_tracking_refs("260706v7pn6e5h")

        self.assertEqual(references, [("ANBL000008245", "kex")])
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://project.supabase.co/rest/v1/shopee_order_mapping")
        self.assertEqual(kwargs["params"]["order_number"], "eq.260706V7PN6E5H")
        self.assertEqual(kwargs["headers"]["apikey"], "sb_secret_test")
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_finds_order_number_from_tracking_number(self):
        session = FakeSession([FakeResponse([{"order_number": "260706v7pn6e5h"}])])
        store = SupabaseMappingStore(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )

        order_number = store.get_order_for_tracking("anbl000008245")

        self.assertEqual(order_number, "260706V7PN6E5H")
        params = session.calls[0][2]["params"]
        self.assertEqual(params["tracking_number"], "eq.ANBL000008245")
        self.assertEqual(params["limit"], "1")

    def test_upsert_preserves_known_carrier_over_auto(self):
        session = FakeSession()
        store = SupabaseMappingStore(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )
        references = [
            ShopeeTrackingRef("260706V7PN6E5H", "ANBL000008245", "auto"),
            ShopeeTrackingRef("260706V7PN6E5H", "ANBL000008245", "kex"),
            ShopeeTrackingRef("260706V7PN6E5H", "ANBL26F000006319", "auto"),
        ]

        imported = store.upsert_references(references)

        self.assertEqual(imported, 2)
        self.assertEqual(len(session.calls), 2)
        known_call, auto_call = session.calls
        self.assertIn("resolution=merge-duplicates", known_call[2]["headers"]["Prefer"])
        self.assertEqual(known_call[2]["json"][0]["carrier"], "kex")
        self.assertIn("resolution=ignore-duplicates", auto_call[2]["headers"]["Prefer"])
        self.assertEqual(auto_call[2]["json"][0]["carrier"], "auto")

    def test_legacy_jwt_key_uses_authorization_header(self):
        session = FakeSession([FakeResponse([])])
        store = SupabaseMappingStore(
            "https://project.supabase.co",
            "eyJlegacy-service-role",
            session=session,
        )

        store.get_tracking_refs("260706V7PN6E5H")

        headers = session.calls[0][2]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer eyJlegacy-service-role")

    def test_prunes_rows_before_cutoff(self):
        session = FakeSession([FakeResponse([{"order_number": "260501OLD0001"}])])
        store = SupabaseMappingStore(
            "https://project.supabase.co",
            "sb_secret_test",
            session=session,
        )

        deleted = store.prune_before("260524")

        self.assertEqual(deleted, 1)
        self.assertEqual(session.calls[0][0], "DELETE")
        self.assertEqual(session.calls[0][2]["params"]["order_number"], "lt.260524")


if __name__ == "__main__":
    unittest.main()
