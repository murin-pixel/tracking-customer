import unittest

from klean_pod_checker.proof_tokens import (
    local_kex_proof_parts,
    make_sheet_proof_token,
    read_sheet_proof_token,
)


class ProofTokenTests(unittest.TestCase):
    secret = "x" * 64

    def test_round_trips_approved_kex_source(self):
        source = "/proof/kex/ANBL000005925/proof-1-1234567890.jpg"
        token = make_sheet_proof_token(source, self.secret)
        self.assertEqual(read_sheet_proof_token(token, self.secret), source)
        self.assertEqual(
            local_kex_proof_parts(source),
            ("ANBL000005925", "proof-1-1234567890.jpg"),
        )

    def test_rejects_tampered_or_unapproved_sources(self):
        with self.assertRaises(ValueError):
            make_sheet_proof_token("https://example.test/image.jpg", self.secret)
        token = make_sheet_proof_token(
            "https://www.skyfrog.net/store/C000000/pod/proof.jpg", self.secret
        )
        with self.assertRaises(ValueError):
            read_sheet_proof_token(token + "x", self.secret)


if __name__ == "__main__":
    unittest.main()
