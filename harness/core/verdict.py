"""决策与裁决对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    DRYRUN = "dryrun"
    FORK = "fork"
    ESCALATE = "escalate"


@dataclass
class Verdict:
    """策略层输出的裁决。

    source 标明这条裁决由谁给出：hard_gate / preval / scheduler / budget，
    便于后面统计「否决权由谁承担」。
    """

    decision: Decision
    reason: str = ""
    source: str = ""
    score: float | None = None
    cost: float = 0.0
    isolation: str = "none"  # none / dryrun / fork / human
    checks: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # 供演示/分析用的附加度量（不进账本 schema）


@dataclass
class ApplyResult:
    """在世界（或影子世界）上执行一次动作的结果。"""

    ok: bool
    detail: str = ""
    data: dict[str, Any] | None = None
