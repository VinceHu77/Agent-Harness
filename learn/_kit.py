"""学习用装配器：把一次动作需要的零件拼起来。

它和 `harness/bench/runner.py` 的装配方式**完全同构**，只是去掉了场景集和 CSV，
让你能在十几行里看清「谁依赖谁」。学完这 6 个脚本，再去看 runner.py 会很轻松。
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

# 让脚本可以直接 `python learn/xxx.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.core.action import Action                       # noqa: E402
from harness.core.budget import Budget                       # noqa: E402
from harness.core.context import RunContext                  # noqa: E402
from harness.core.cost import CostModel                      # noqa: E402
from harness.core.engine import Engine                       # noqa: E402
from harness.core.ledger import Ledger                       # noqa: E402
from harness.env.world import World                          # noqa: E402
from harness.gates.hard_gate import HardGate                 # noqa: E402
from harness.gates.preval import PreVal                      # noqa: E402
from harness.sched.policies import POLICIES, Policy          # noqa: E402
from harness.state.cow import build_session_state            # noqa: E402
from harness.state.fork import ForkManager                   # noqa: E402


def title(text: str) -> None:
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def section(text: str) -> None:
    print()
    print(f"── {text} " + "─" * max(0, 66 - len(text)))


def build(policy: str = "OURS", budget: float = 50.0, phi: float = 0.6,
          orders: int = 20, l_units: float | None = None) -> SimpleNamespace:
    """装配一个可执行的最小运行时。

    顺序很重要：世界 → 门控 → 评分器 → 状态 → fork 管理器 → 上下文 → 引擎。
    除了引擎，其他都是被引擎「用」的零件。
    """
    cfg = POLICIES[policy]
    if l_units is not None:
        cfg = replace(cfg, l_units=l_units)  # 只改损失权重 L，其余不变
    world = World()
    ledger = Ledger()
    bud = Budget(budget)
    gate = HardGate(world, enabled=cfg.use_gate)
    preval = PreVal(tau_deny=cfg.tau_deny)
    state = build_session_state(orders_hint=orders)
    fork = ForkManager(world, state, CostModel(), cfg.snapshot)
    ctx = RunContext(
        world=world, fork=fork, gate=gate, preval=preval, budget=bud,
        cost=CostModel(), state=state, phi=phi,
        run_id=f"learn-{policy}", scenario_id="LEARN", session_id="learn",
        shadow_bytes_estimate=world.shadow_bytes_estimate(),
    )
    engine = Engine(ctx, Policy(cfg), ledger)
    return SimpleNamespace(world=world, ledger=ledger, budget=bud, gate=gate,
                           preval=preval, state=state, fork=fork, ctx=ctx,
                           engine=engine, cfg=cfg)


def act(tool: str, args: dict, rid: str = "a1", text: str = "",
        ns: str = "ns_A") -> Action:
    return Action(request_id=rid, session_id="learn", tool=tool, args=args,
                  namespace=ns, user_text=text)
