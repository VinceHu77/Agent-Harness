"""P0 验证：门控边界、fork 能力、C1 泄漏、预算降级、可复现性。"""

from __future__ import annotations

import unittest

from harness.core.action import Action, Tier, spec_for
from harness.core.cost import CostModel
from harness.core.engine import Engine
from harness.core.ledger import Ledger
from harness.env.world import World
from harness.gates.hard_gate import HardGate
from harness.gates.preval import PreVal
from harness.state.cow import build_session_state
from harness.state.fork import ForkManager


def act(tool: str, args: dict, rid: str = "t1", text: str = "", ns: str = "ns_A") -> Action:
    return Action(request_id=rid, session_id="s", tool=tool, args=args,
                  namespace=ns, user_text=text)


class TestGate(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.gate = HardGate(self.world)

    def tearDown(self) -> None:
        self.world.close()

    def test_blocks_cross_tenant(self) -> None:
        res = self.gate.check(act("refund_execute", {"order_id": "ORDER-1002", "amount": 5000}))
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "L2-NAMESPACE")

    def test_blocks_over_order_amount(self) -> None:
        res = self.gate.check(act("refund_execute", {"order_id": "ORDER-1001", "amount": 200}))
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "L2-OVER-ORDER")

    def test_blocks_missing_approval_above_threshold(self) -> None:
        res = self.gate.check(act("refund_execute", {"order_id": "ORDER-1004", "amount": 4800}))
        self.assertFalse(res.passed)
        self.assertEqual(res.code, "L2-NO-APPROVAL")

    def test_allows_valid_approval(self) -> None:
        res = self.gate.check(act("refund_execute", {
            "order_id": "ORDER-1004", "amount": 4200, "approval_token": "TOKEN-MGR-1"}))
        self.assertTrue(res.passed, res)

    def test_stateless_gate_passes_each_leg_of_split_refund(self) -> None:
        """两笔 3000 各自合法 → 无状态硬门控必然放行，累计越权交给 fork。"""
        a = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="a1")
        self.assertTrue(self.gate.check(a).passed)
        self.world.apply(a)  # 第一笔真实执行
        self.assertTrue(self.gate.check(a).passed)


class TestForkAndC1(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.state = build_session_state(orders_hint=20)
        self.fork = ForkManager(self.world, self.state, CostModel(), "delta")

    def tearDown(self) -> None:
        self.world.close()

    def test_fork_catches_split_refund_without_touching_world(self) -> None:
        a = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="a1")
        self.world.apply(a)
        h = self.world.state_hash()
        b = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="a2")
        res = self.fork.run(b)
        self.assertTrue(res.danger)
        self.assertIn("C-OVERREFUND", res.codes)
        self.assertEqual(self.world.state_hash(), h, "fork 不得改变真实世界")

    def test_fork_allows_legal_partial_refunds(self) -> None:
        self.world.apply(act("refund_execute", {"order_id": "ORDER-1008", "amount": 300}, rid="b1"))
        res = self.fork.run(act("refund_execute", {"order_id": "ORDER-1008", "amount": 200},
                                rid="b2"))
        self.assertFalse(res.danger, res.codes)

    def test_c1_side_effect_is_not_rollbackable(self) -> None:
        """C1 的实证：状态可以回滚，已发出的副作用不能。"""
        h0 = self.world.state_hash()
        state = self.state.fork("branch-1")
        state.set("draft", {"plan": "全额退款"})
        # 分支里直接调用了真实工具（这是 fork 无法阻止的情形）
        self.world.apply(act("refund_execute", {"order_id": "ORDER-1004", "amount": 100},
                             rid="c1"), branch_id="branch-1")
        leaked = self.world.state_hash() != h0
        # 丢弃分支状态（等价于 rollback）
        del state
        self.assertTrue(leaked, "副作用必须已经落地")
        self.assertNotEqual(self.world.state_hash(), h0, "回滚状态无法撤销外部副作用")
        n = self.world.conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]
        self.assertEqual(n, 1)

    def test_snapshot_granularity_is_measurable(self) -> None:
        child = self.state.fork("b")
        child.set("probe", "x")
        delta, full = child.snapshot_bytes("delta"), child.snapshot_bytes("full")
        self.assertGreater(full, delta * 10, "增量快照应显著小于全量快照")


class TestDryRunFidelity(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()

    def tearDown(self) -> None:
        self.world.close()

    def test_phi_one_is_faithful(self) -> None:
        a = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="p1")
        self.world.apply(a)
        b = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="p2")
        danger, codes = self.world.preview(b, phi=1.0)
        self.assertTrue(danger)
        self.assertIn("C-OVERREFUND", codes)

    def test_phi_zero_never_reports_truth(self) -> None:
        a = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="q1")
        self.world.apply(a)
        b = act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000}, rid="q2")
        danger, _ = self.world.preview(b, phi=0.0)
        # φ=0 时预演必然失真：要么漏报（False），要么误报（True+FALSE-ALARM）
        self.assertFalse(danger)

    def test_preview_has_no_side_effect(self) -> None:
        h = self.world.state_hash()
        self.world.preview(act("refund_execute", {"order_id": "ORDER-1007", "amount": 3000},
                               rid="r1"), phi=1.0)
        self.assertEqual(self.world.state_hash(), h)


class TestPolicyAndBudget(unittest.TestCase):
    def _run(self, policy: str, budget: float, phi: float = 0.6) -> list[dict]:
        from harness.bench.loader import load_scenarios
        from harness.bench.runner import Runner

        runner = Runner(policies=[policy], budgets=[budget], phi=phi,
                        scenarios=load_scenarios())
        res = runner.run_one(policy, budget)
        return res.ledger_rows

    def test_determinism(self) -> None:
        a = self._run("OURS", 300)
        b = self._run("OURS", 300)
        self.assertEqual([r["decision"] for r in a], [r["decision"] for r in b])
        self.assertEqual([r["cost"] for r in a], [r["cost"] for r in b])

    def test_budget_exhaustion_degrades_gracefully(self) -> None:
        rows = self._run("B2", 5.0)
        self.assertTrue(any("fallback_deny" in r["reason"] for r in rows))
        self.assertFalse(any("Traceback" in (r["reason"] or "") for r in rows))

    def test_unknown_tool_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            spec_for("rm_rf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
