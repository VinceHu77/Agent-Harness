"""统一引擎：Agent 动作 → ①硬门控 → ②PreVal → ③策略 → 执行 → 账本。

这是 P0 的核心收口点：所有基线、消融、场景都走同一条路径，
因此「同一预算 B、同一场景集、同一评分噪声」下的比较才有意义。
"""

from __future__ import annotations

import time
from typing import Any

from .action import Action
from .verdict import Decision, Verdict


class Engine:
    def __init__(self, ctx: Any, policy: Any, ledger: Any) -> None:
        self.ctx = ctx
        self.policy = policy
        self.ledger = ledger

    def step(self, action: Action) -> dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self.ctx

        gate_res = ctx.gate.check(action)
        score = ctx.preval.score(action)

        if not gate_res.passed:
            verdict = Verdict(Decision.DENY, f"{gate_res.code}:{gate_res.detail}",
                              "hard_gate", score=score, cost=ctx.cost.allow)
        else:
            verdict = self.policy.decide(action, ctx, score)

        executed = False
        outcome = verdict.decision.value
        world_hash = ""
        if verdict.decision is Decision.ALLOW:
            res = ctx.world.apply(action)
            executed = res.ok
            outcome = res.detail if res.ok else f"apply_failed:{res.detail}"
            if executed:
                ctx.state.set("turn", int(ctx.state.get("turn", 0)) + 1)
                ctx.state.set("last_request", action.request_id)
                results = list(ctx.state.get("tool_results", []))
                results.append({"request_id": action.request_id, "tool": action.tool})
                ctx.state.set("tool_results", results[-20:])
            world_hash = ctx.world.state_hash()
        else:
            world_hash = ctx.world.state_hash()

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record = {
            "run_id": ctx.run_id,
            "scenario_id": ctx.scenario_id,
            "request_id": action.request_id,
            "session_id": ctx.session_id,
            "branch_id": action.branch_id,
            "tool": action.tool,
            "tier": int(action.tier),
            "irreversible": int(action.irreversible),
            "arg_digest": action.arg_digest,
            "idem_key": action.idem_key,
            "decision": verdict.decision.value,
            "reason": verdict.reason,
            "source": verdict.source,
            "risk_score": verdict.score if verdict.score is not None else score,
            "cost": round(verdict.cost, 4),
            "isolation": verdict.isolation,
            "executed": int(executed),
            "outcome": outcome,
            "world_hash": world_hash,
            "elapsed_ms": round(elapsed_ms, 3),
            "gate_code": gate_res.code,
            "gate_detail": gate_res.detail,
            "gate_passed": int(gate_res.passed),
            "extra": verdict.extra,
        }
        # 账本只收 schema 内字段；gate_code/extra 等仅供演示与分析使用
        self.ledger.append(**{k: v for k, v in record.items() if k != "extra"})
        record["checks"] = list(verdict.checks)
        return record
