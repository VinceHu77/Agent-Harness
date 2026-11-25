"""客服世界：建表、种子数据、只读查询。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import world


class TestWorld(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "world.db"
        world.reset(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_seed_counts(self) -> None:
        self.assertEqual(len(world.query("SELECT * FROM customers", path=self.db)), 3)
        self.assertEqual(len(world.query("SELECT * FROM orders", path=self.db)), 5)
        self.assertEqual(len(world.query("SELECT * FROM coupons", path=self.db)), 2)
        self.assertEqual(len(world.query("SELECT * FROM tickets", path=self.db)), 0)

    def test_query_returns_dict_rows(self) -> None:
        row = world.query(
            "SELECT * FROM orders WHERE id = ?", ("ORD-1001",), path=self.db)[0]
        self.assertEqual(row["customer_id"], "CUST-01")
        self.assertEqual(row["paid_cents"], 30000)
        self.assertEqual(row["refunded_cents"], 0)

    def test_partially_refunded_order(self) -> None:
        row = world.query(
            "SELECT * FROM orders WHERE id = ?", ("ORD-1004",), path=self.db)[0]
        self.assertEqual(row["refunded_cents"], 5000)

    def test_reset_is_idempotent(self) -> None:
        world.reset(self.db)
        world.reset(self.db)
        self.assertEqual(len(world.query("SELECT * FROM orders", path=self.db)), 5)

    def test_seed_is_idempotent(self) -> None:
        world.seed(self.db)
        world.seed(self.db)
        self.assertEqual(len(world.query("SELECT * FROM customers", path=self.db)), 3)


if __name__ == "__main__":
    unittest.main()
