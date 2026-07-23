import sqlite3
import tempfile
import unittest
from pathlib import Path

from klean_pod_checker.migrate_sqlite_mapping import read_legacy_references


class MigrateSqliteMappingTests(unittest.TestCase):
    def test_reads_legacy_tables_and_prefers_known_carrier(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "cache.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE shopee_order_tracking (
                    order_number TEXT,
                    tracking_number TEXT,
                    carrier TEXT,
                    imported_at TEXT
                );
                CREATE TABLE shopee_mapping_order (
                    order_number TEXT,
                    tracking_number TEXT,
                    imported_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO shopee_order_tracking VALUES (?, ?, ?, ?)",
                ("260706ORDER001", "ANBL000000001", "auto", "2026-07-20"),
            )
            connection.execute(
                "INSERT INTO shopee_mapping_order VALUES (?, ?, ?)",
                (
                    "260706ORDER001",
                    "KEX_เลขพัสดุ_ANBL000000001",
                    "2026-07-21",
                ),
            )
            connection.commit()
            connection.close()

            references = read_legacy_references(path)

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].order_number, "260706ORDER001")
        self.assertEqual(references[0].tracking_number, "ANBL000000001")
        self.assertEqual(references[0].carrier, "kex")


if __name__ == "__main__":
    unittest.main()
