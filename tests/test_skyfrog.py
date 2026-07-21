import unittest

from klean_pod_checker.skyfrog import SkyfrogClient, STATUS_LABELS


class SkyfrogTests(unittest.TestCase):
    def test_status_mapping(self):
        self.assertEqual(STATUS_LABELS["C"], ("Completed", "จัดส่งสำเร็จ"))
        self.assertEqual(STATUS_LABELS["B"], ("Open", "เปิดงาน"))

    def test_proof_urls_are_deduplicated(self):
        client = SkyfrogClient("C000000", "system", "secret")
        row = {
            "rsignimg": "Sign-R-1.jpg",
            "dsignimg": "Sign-D-1.jpg",
            "Upload5Pic": [{"filename": "extra 1.png"}, {"filename": "Sign-D-1.jpg"}],
            "attachfile": "invoice.pdf",
        }
        self.assertEqual(
            client._proof_urls(row),
            [
                "https://www.skyfrog.net/store/C000000/pod/Sign-R-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/Sign-D-1.jpg",
                "https://www.skyfrog.net/store/C000000/pod/extra%201.png",
            ],
        )


if __name__ == "__main__":
    unittest.main()
