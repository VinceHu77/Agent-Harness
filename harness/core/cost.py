"""成本模型（P0 为可参数化的合成模型，P1 需按真实分钟/内存标定）。

单位：抽象成本单元（1 unit ≈ 一次轻量校验）。所有常数集中在 dataclass 里，
运行器会把它们写进结果，保证「同一预算 B 下比较」这件事可复现、可重算。

成本构成（这是 RC1/RC2 要分解的量）：
  allow   = ledger 写一次
  dryrun  = 影子执行一次（不含状态快照）
  fork    = fork_base + 状态快照字节 + 影子世界字节
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    allow: float = 0.01
    dryrun: float = 0.5
    fork_base: float = 2.0
    # 每移动 1MB 状态记多少成本；影子世界与状态快照共用这一费率
    per_mb: float = 100.0

    def fork(self, state_bytes: int, shadow_bytes: int) -> float:
        moved = (state_bytes + shadow_bytes) / 1_000_000.0
        return self.fork_base + self.per_mb * moved

    def describe(self) -> dict[str, float]:
        return {
            "allow": self.allow,
            "dryrun": self.dryrun,
            "fork_base": self.fork_base,
            "per_mb": self.per_mb,
        }
