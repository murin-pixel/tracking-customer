import unittest

from klean_pod_checker.sheets import (
    column_letter_to_index,
    extract_order_refs,
    normalize_auto_search_input,
    normalize_order_input,
    normalize_tracking_input,
    parse_order_date,
)


class SheetTests(unittest.TestCase):
    def test_column_letters(self):
        self.assertEqual(column_letter_to_index("A"), 0)
        self.assertEqual(column_letter_to_index("G"), 6)
        self.assertEqual(column_letter_to_index("AA"), 26)

    def test_extracts_and_merges_orders(self):
        csv_text = (
            "A,B,C,D,E,F,Tracking No. (กรณีตามการจัดส่ง)\n"
            "1,2,3,4,5,6,# KLEAN&KARE_260607E69813MF\n"
            "1,2,3,4,5,6,KLEAN & KARE _ 260607E69813MF\n"
            "1,2,3,4,5,6,KLEAN&KARE_260608G0EEY23B\n"
            "1,2,3,4,5,6,KEX_เลขพัสดุ_ANBL000005925\n"
            "1,2,3,4,5,6,INTEREXPRESS *เลขพัสดุ* ANBL26F000006319\n"
        )
        refs = extract_order_refs(csv_text)
        self.assertEqual(
            [ref.order_number for ref in refs],
            [
                "260607E69813MF",
                "260608G0EEY23B",
                "ANBL000005925",
                "ANBL26F000006319",
            ],
        )
        self.assertEqual(refs[0].sheet_rows, [2, 3])
        self.assertEqual(refs[0].order_date.isoformat(), "2026-06-07")
        self.assertEqual(refs[2].carrier, "kex")
        self.assertIsNone(refs[2].order_date)
        self.assertEqual(refs[3].carrier, "interexpress")

    def test_order_date_validation(self):
        self.assertEqual(parse_order_date("260607E69813MF").isoformat(), "2026-06-07")
        self.assertIsNone(parse_order_date("261307INVALID"))
        self.assertIsNone(parse_order_date("ABC"))

    def test_normalizes_cs_order_input(self):
        self.assertEqual(normalize_order_input("260608g0eey23b"), "260608G0EEY23B")
        self.assertEqual(
            normalize_order_input(" # KLEAN & KARE _ 260608G0EEY23B "),
            "260608G0EEY23B",
        )
        with self.assertRaises(ValueError):
            normalize_order_input("KLEAN&KARE_missing")
        self.assertEqual(
            normalize_tracking_input("KEX_เลขพัสดุ_ANBL000005925"),
            ("kex", "ANBL000005925"),
        )
        self.assertEqual(
            normalize_tracking_input("anbl000005925", "kex"),
            ("kex", "ANBL000005925"),
        )
        self.assertEqual(
            normalize_tracking_input("INTEREXPRESS *เลขพัสดุ* anbl26f000006319"),
            ("interexpress", "ANBL26F000006319"),
        )
        self.assertEqual(
            normalize_tracking_input("ANBL26F000006103", "interexpress"),
            ("interexpress", "ANBL26F000006103"),
        )
        with self.assertRaisesRegex(ValueError, "เลือกผู้ให้บริการ"):
            normalize_tracking_input("ANBL26F000006103")

    def test_normalizes_auto_search_without_selecting_a_carrier(self):
        self.assertEqual(
            normalize_auto_search_input("KEX_เลขพัสดุ_ANBL000005925"),
            "ANBL000005925",
        )
        self.assertEqual(
            normalize_auto_search_input("INTEREXPRESS *เลขพัสดุ* ANBL26F000006319"),
            "ANBL26F000006319",
        )
        self.assertEqual(
            normalize_auto_search_input("260608g0eey23b"),
            "260608G0EEY23B",
        )


if __name__ == "__main__":
    unittest.main()
