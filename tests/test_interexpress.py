import unittest

from klean_pod_checker.interexpress import InterexpressClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"token": {"accessToken": "test-token"}})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(
            {
                "shipmentNo": "ANBL26F000006319",
                "lastStatusCode": "POD",
                "ttDisplayRemarks": "พัสดุจัดส่งสำเร็จ",
                "actPickupDt": "2026-07-01T09:00:00+07:00",
                "actDeliveryDt": "2026-07-02T22:53:00+07:00",
                "lastStatusDt": "2026-07-02T22:53:00+07:00",
                "recipientName": "Must not persist",
                "recipientPhoneNo": "0000000000",
            }
        )

    def close(self):
        return None


class InterexpressClientTests(unittest.TestCase):
    def test_reads_status_without_retaining_recipient_information(self):
        session = FakeSession()
        client = InterexpressClient("ANBLAdmin", "secret", session=session)
        result = client.search_order("anbl26f000006319")

        self.assertTrue(result.found)
        self.assertTrue(result.delivered)
        self.assertEqual(result.status_code, "POD")
        self.assertEqual(result.status_th, "พัสดุจัดส่งสำเร็จ")
        self.assertEqual(result.group_name, "InterExpress")
        self.assertEqual(result.driver, "")
        self.assertEqual(result.raw, {})
        self.assertNotIn("Must not persist", str(result))
        self.assertEqual(session.calls[0][2]["json"]["type"], "corporate")
        self.assertEqual(
            session.calls[1][2]["headers"]["Authorization"], "Bearer test-token"
        )


if __name__ == "__main__":
    unittest.main()
