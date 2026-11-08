"""运行上下文：策略层需要的一切依赖都从这里拿。

策略只依赖 ctx 暴露的接口（world / fork / gate / preval / budget / cost / phi），
因此换基线或换消融变体时，不需要改引擎。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .budget import Budget
from .cost import CostModel


@dataclass
class RunContext:
    world: Any
    fork: Any
    gate: Any
    preval: Any
    budget: Budget
    cost: CostModel
    state: Any
    phi: float
    run_id: str = "run"
    scenario_id: str = ""
    session_id: str = ""
    shadow_bytes_estimate: int = 0
    extras: dict[str, Any] = field(default_factory=dict)
