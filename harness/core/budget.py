"""运行级预算：一次评测（一个策略跑一整套场景）共享一个预算 B。

这正是开题报告里 C2 与调度问题的约束 `C(π) ≤ B` 的落地方式：
预算不是 per-action 的开关，而是整轮实验共享的资源，花完就降级。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Budget:
    total: float
    spent: float = 0.0
    exhausted_count: int = 0
    spent_by: dict[str, float] = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    def can(self, cost: float) -> bool:
        return self.spent + cost <= self.total + 1e-9

    def spend(self, cost: float, kind: str = "other") -> bool:
        """扣费。预算不足时返回 False 并计数，不抛异常（供调度器降级）。"""
        if not self.can(cost):
            self.exhausted_count += 1
            return False
        self.spent += cost
        self.spent_by[kind] = self.spent_by.get(kind, 0.0) + cost
        return True

    def note_exhausted(self) -> None:
        """预算不足导致降级时记账（供「预算耗尽次数」指标使用）。"""
        self.exhausted_count += 1

    def charge(self, cost: float, kind: str = "other") -> None:
        """无条件扣费（用于已经发生的成本，如实际 fork 比估算更贵）。"""
        self.spent += cost
        self.spent_by[kind] = self.spent_by.get(kind, 0.0) + cost

    def snapshot(self) -> dict[str, object]:
        return {
            "budget": self.total,
            "spent": round(self.spent, 3),
            "remaining": round(self.remaining, 3),
            "exhausted_count": self.exhausted_count,
            "spent_by": {k: round(v, 3) for k, v in self.spent_by.items()},
        }
