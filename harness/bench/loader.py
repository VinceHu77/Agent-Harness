"""场景加载。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).with_name("scenarios.json")


@dataclass
class Step:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    dangerous: bool = False
    required: bool = False


@dataclass
class Scenario:
    id: str
    kind: str                     # attack | benign
    attack_type: str
    namespace: str
    user_text: str
    steps: list[Step]

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"


def load_scenarios(path: str | Path | None = None) -> list[Scenario]:
    p = Path(path) if path else DEFAULT_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: list[Scenario] = []
    for s in raw["scenarios"]:
        steps = [
            Step(
                tool=st["tool"],
                args=st.get("args", {}),
                dangerous=bool(st.get("dangerous", False)),
                required=bool(st.get("required", False)),
            )
            for st in s["steps"]
        ]
        out.append(
            Scenario(
                id=s["id"],
                kind=s["kind"],
                attack_type=s.get("attack_type", "none"),
                namespace=s.get("namespace", "ns_A"),
                user_text=s.get("user_text", ""),
                steps=steps,
            )
        )
    return out
