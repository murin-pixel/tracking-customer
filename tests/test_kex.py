import tempfile
import unittest
from pathlib import Path

from klean_pod_checker.kex import KexClient


class FakeResponse:
    def __init__(self, payload=None, *, content=b"", content_type="application/json"):
        self.payload = payload
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, _size):
        yield self.content


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/VerifyPhone"):
            return FakeResponse(
                {
                    "verifySta": {"code": "01"},
                    "verifyShipment_Status": [{"courier_name": "KEX Driver"}],
                }
            )
        return FakeResponse(
            [
                {
                    "tracking_no": "ANBL000005925",
                    "ref": {
                        "icon": {
                            "current_idx": 3,
                            "display": [
                                {"code": "101", "desc": "รับพัสดุแล้ว"},
                                {"code": "200", "desc": "อยู่ระหว่างขนส่ง"},
                                {"code": "300", "desc": "กำลังจัดส่ง"},
                                {"code": "400", "desc": "จัดส่งสำเร็จ"},
                            ],
                        },
                        "shipment": {
                            "pickup_date": "2026-06-27T18:07:32",
                            "epod_photo": [
                                "https://proof.myhuaweicloud.com/one.jpg",
                                "https://proof.myhuaweicloud.com/two.jpg",
                            ],
                            "sig": "",
                            "img": None,
                        },
                        "shipment_status": [
                            {
                                "s_code": "POD",
                                "s_desc": "จัดส่งพัสดุสำเร็จ",
                                "s_datetime": "2026-06-28T12:16:13",
                                "loc": "กุฉินารายณ์, กาฬสินธุ์",
                            },
                            {
                                "s_code": "010",
                                "s_desc": "พนักงานเข้ารับพัสดุแล้ว",
                                "s_datetime": "2026-06-27T18:07:32",
                                "loc": "บางพลี, สมุทรปราการ",
                            },
                        ],
                    },
                }
            ]
        )

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse(content=b"\xff\xd8fake-jpeg", content_type="image/jpg")

    def close(self):
        return None


class KexClientTests(unittest.TestCase):
    def test_verifies_pin_and_stores_proofs_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            session = FakeSession()
            client = KexClient("0000", Path(directory), session=session)
            result = client.search_order("anbl000005925")

            self.assertTrue(result.delivered)
            self.assertEqual(result.status_code, "POD")
            self.assertEqual(result.group_name, "KEX")
            self.assertEqual(result.location, "กุฉินารายณ์, กาฬสินธุ์")
            self.assertEqual(result.driver, "KEX Driver")
            self.assertEqual(len(result.proof_urls), 2)
            self.assertTrue(all(url.startswith("/proof/kex/") for url in result.proof_urls))
            self.assertEqual(len(session.gets), 2)
            self.assertEqual(session.posts[1][1]["json"]["verifyCode"], "0000")
            self.assertEqual(len(list(Path(directory).rglob("*.jpg"))), 2)
            self.assertEqual(result.raw, {})


if __name__ == "__main__":
    unittest.main()
