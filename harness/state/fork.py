"""Fork 管理器：影子世界 + 状态分叉 + 后果判定 + 成本度量。

一次 fork 做四件事（缺一不可）：
  1. 状态分叉（COW，delta 或 full）—— 隔离的是 Agent 自己的状态；
  2. 复制影子世界 —— 因为要「执行一次看后果」，而真实副作用不能发生；
  3. 在影子上执行同一动作，比较执行前后的后果不变量；
  4. 记录状态字节 / 影子字节 / 耗时 —— 这是 RC2 的原始数据。

关键设计：fork 的「裁决 oracle」是**不变量检查**，不是 PreVal 分数。
所以即使评分器漏报，fork 依然能拦住组合型越权（这正是联合设计的价值）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.action import Action
from ..core.cost import CostModel
from ..core.verdict import ApplyResult


@dataclass
class ForkResult:
    danger: bool
    codes: tuple[str, ...] = ()
    cost: float = 0.0
    state_bytes: int = 0
    shadow_bytes: int = 0
    elapsed_ms: float = 0.0
    applied: ApplyResult | None = None
    changes: dict[str, Any] = field(default_factory=dict)


class ForkManager:
    def __init__(self, world: Any, state: Any, cost_model: CostModel, snapshot_mode: str = "delta"):
        self.world = world
        self.state = state
        self.cost = cost_model
        self.snapshot_mode = snapshot_mode
        self.runs = 0
        self.total_cost = 0.0
        self.total_state_bytes = 0
        self.total_shadow_bytes = 0
        self.latencies_ms: list[float] = []
        # 首次 fork 之后用实测影子字节数替代估算，避免预算判断系统性偏低
        self._measured_shadow_bytes = 0

    # ------------------------------------------------------------------ 估算
    def _estimate_state_bytes(self) -> int:
        """fork 前对「要搬多少状态」的估算。

        delta 模式下子分支初始只有探针写入（几十字节），
        不能用父状态的全量体积去估算 —— 那是 A2/全量快照的语义。
        """
        if self.snapshot_mode == "full":
            return self.state.full_bytes()
        return 256

    def estimate(self, action: Action | None = None) -> float:
        shadow = self._measured_shadow_bytes or self.world.shadow_bytes_estimate()
        return self.cost.fork(self._estimate_state_bytes(), shadow)

    # ------------------------------------------------------------------ 执行
    def run(self, action: Action, branch_id: str | None = None) -> ForkResult:
        t0 = time.perf_counter()
        br = branch_id or f"fork:{action.request_id}"

        child = self.state.fork(br)
        child.set("probe", action.request_id)
        child.set("turn", int(self.state.get("turn", 0)) + 1)
        state_bytes = child.snapshot_bytes(self.snapshot_mode)

        shadow = self.world.shadow_conn()
        try:
            before = self.world.violations(self.world.conn)
            res = self.world.apply(action.with_branch(br), conn=shadow, record=False)
            after = self.world.violations(shadow)
            codes = tuple(sorted(after - before))
            shadow_bytes = self.world.conn_bytes(shadow)
        finally:
            shadow.close()

        cost = self.cost.fork(state_bytes, shadow_bytes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.runs += 1
        self.total_cost += cost
        self.total_state_bytes += state_bytes
        self.total_shadow_bytes += shadow_bytes
        self.latencies_ms.append(elapsed_ms)
        self._measured_shadow_bytes = max(self._measured_shadow_bytes, shadow_bytes)

        return ForkResult(
            danger=bool(codes),
            codes=codes,
            cost=cost,
            state_bytes=state_bytes,
            shadow_bytes=shadow_bytes,
            elapsed_ms=elapsed_ms,
            applied=res,
            changes={"branch": br, "snapshot_mode": self.snapshot_mode},
        )

    def stats(self) -> dict[str, Any]:
        lat = self.latencies_ms
        return {
            "fork_runs": self.runs,
            "fork_cost": round(self.total_cost, 3),
            "state_bytes": self.total_state_bytes,
            "shadow_bytes": self.total_shadow_bytes,
            "snapshot_mode": self.snapshot_mode,
            "avg_fork_ms": round(sum(lat) / len(lat), 3) if lat else 0.0,
            "p95_fork_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 3) if lat else 0.0,
        }
