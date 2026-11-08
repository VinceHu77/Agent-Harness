"""运行器：在相同预算 B / 相同场景 / 相同噪声下跑完整个策略矩阵。

产物（默认 runs/p0/）：
  summary.csv        每个 (policy, budget, phi) 一行：IR/FAR/CR/成本/延迟
  per_scenario.csv   每个 (policy, budget, scenario) 一行：是否泄漏/完成/误拦
  ledger.csv         全部账本记录（含分支来源与执行后世界哈希），可独立审计
  meta.json          成本模型常数、场景数、耗时 —— 保证结果可重算
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from ..core.action import Action
from ..core.budget import Budget
from ..core.context import RunContext
from ..core.cost import CostModel
from ..core.engine import Engine
from ..core.ledger import Ledger
from ..env.world import World
from ..gates.hard_gate import HardGate
from ..gates.preval import PreVal
from ..sched.policies import POLICIES, Policy, PolicyConfig
from ..state.cow import build_session_state
from ..state.fork import ForkManager
from .loader import Scenario, load_scenarios
from .metrics import SUMMARY_FIELDS, summarize


@dataclass
class RunResult:
    policy: str
    budget: float
    phi: float
    summary: dict[str, Any]
    per_scenario: list[dict[str, Any]]
    ledger_rows: list[dict[str, Any]]
    scenario_bytes: int


class Runner:
    def __init__(
        self,
        policies: Iterable[str] | None = None,
        budgets: Iterable[float] | None = None,
        phi: float = 0.6,
        cost_model: CostModel | None = None,
        scenarios: list[Scenario] | None = None,
        l_units: float | None = None,
    ) -> None:
        self.policies = list(policies) if policies else ["B1", "B2", "B3", "OURS"]
        self.budgets = list(budgets) if budgets else [300.0]
        self.phi = phi
        self.cost = cost_model or CostModel()
        self.scenarios = scenarios or load_scenarios()
        # 损失权重 L 是 V_fork 的可调参数；P0 默认 55，P1 应做 L 的敏感性扫描
        self.l_units = l_units
        self.results: list[RunResult] = []
        for name in self.policies:
            if name not in POLICIES:
                raise ValueError(f"未知策略：{name}（可选：{sorted(POLICIES)}）")

    # ------------------------------------------------------------------
    def run_one(self, policy_name: str, budget_total: float) -> RunResult:
        cfg: PolicyConfig = POLICIES[policy_name]
        if self.l_units is not None:
            cfg = replace(cfg, l_units=self.l_units)
        world = World()
        ledger = Ledger()
        budget = Budget(total=budget_total)
        gate = HardGate(world, enabled=cfg.use_gate)
        preval = PreVal(tau_deny=cfg.tau_deny)
        policy = Policy(cfg)

        fork_acc = {"fork_runs": 0, "fork_cost": 0.0, "state_bytes": 0,
                    "shadow_bytes": 0, "latencies": []}
        per_scenario: list[dict[str, Any]] = []
        state_full = 0

        for sc in self.scenarios:
            world.reset()
            state = build_session_state()
            state_full = state.full_bytes()
            fork = ForkManager(world, state, self.cost, cfg.snapshot)
            ctx = RunContext(
                world=world,
                fork=fork,
                gate=gate,
                preval=preval,
                budget=budget,
                cost=self.cost,
                state=state,
                phi=self.phi,
                run_id=f"{policy_name}|B{budget_total:g}|phi{self.phi:g}",
                scenario_id=sc.id,
                session_id=f"{policy_name}-B{budget_total:g}-{sc.id}",
                shadow_bytes_estimate=world.shadow_bytes_estimate(),
            )
            engine = Engine(ctx, policy, ledger)

            records: list[dict[str, Any]] = []
            for i, step in enumerate(sc.steps):
                action = Action(
                    request_id=f"{sc.id}:{i}",
                    session_id=ctx.session_id,
                    tool=step.tool,
                    args=dict(step.args),
                    namespace=sc.namespace,
                    user_text=sc.user_text,
                    branch_id="main",
                )
                records.append(engine.step(action))

            leaked = any(
                step.dangerous and rec["executed"] for step, rec in zip(sc.steps, records)
            )
            completed = all(
                rec["executed"] for step, rec in zip(sc.steps, records) if step.required
            )
            falsely_blocked = any(
                step.required and not rec["executed"] for step, rec in zip(sc.steps, records)
            )
            per_scenario.append(
                {
                    "policy": policy_name,
                    "budget": budget_total,
                    "phi": self.phi,
                    "scenario": sc.id,
                    "kind": sc.kind,
                    "attack_type": sc.attack_type,
                    "leaked": int(leaked),
                    "completed": int(completed),
                    "falsely_blocked": int(falsely_blocked),
                    "danger_steps": sum(1 for s in sc.steps if s.dangerous),
                    "danger_executed": sum(
                        1 for s, r in zip(sc.steps, records) if s.dangerous and r["executed"]
                    ),
                    "cost": round(sum(r["cost"] for r in records), 4),
                    "decisions": "|".join(r["decision"] for r in records),
                    "executed_steps": sum(1 for r in records if r["executed"]),
                    "elapsed_ms": round(sum(r["elapsed_ms"] for r in records), 3),
                }
            )

            st = fork.stats()
            fork_acc["fork_runs"] += st["fork_runs"]
            fork_acc["fork_cost"] += st["fork_cost"]
            fork_acc["state_bytes"] += st["state_bytes"]
            fork_acc["shadow_bytes"] += st["shadow_bytes"]
            fork_acc["latencies"].extend(fork.latencies_ms)

        lat = fork_acc["latencies"]
        fork_stats = {
            "fork_runs": fork_acc["fork_runs"],
            "state_bytes": fork_acc["state_bytes"],
            "shadow_bytes": fork_acc["shadow_bytes"],
            "avg_fork_ms": round(sum(lat) / len(lat), 3) if lat else 0.0,
        }
        ledger_rows = ledger.rows()
        summary = summarize(per_scenario, ledger_rows, policy_name, budget.snapshot(),
                            self.phi, fork_stats)
        result = RunResult(policy_name, budget_total, self.phi, summary, per_scenario,
                           ledger_rows, state_full)
        world.close()
        ledger.close()
        self.results.append(result)
        return result

    # ------------------------------------------------------------------
    def run(self) -> list[RunResult]:
        for b in self.budgets:
            for p in self.policies:
                self.run_one(p, b)
        return self.results

    # ------------------------------------------------------------------
    def write(self, outdir: str | Path) -> Path:
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)

        with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
            w.writeheader()
            for r in self.results:
                w.writerow({k: r.summary.get(k) for k in SUMMARY_FIELDS})

        per_fields = list(self.results[0].per_scenario[0].keys()) if self.results else []
        with (out / "per_scenario.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=per_fields)
            w.writeheader()
            for r in self.results:
                w.writerows(r.per_scenario)

        led_fields = ["policy", "budget", "scenario_id", "request_id", "tool", "tier",
                      "decision", "source", "reason", "risk_score", "cost", "isolation",
                      "executed", "branch_id", "idem_key", "arg_digest", "world_hash",
                      "elapsed_ms"]
        with (out / "ledger.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=led_fields, extrasaction="ignore")
            w.writeheader()
            for r in self.results:
                for row in r.ledger_rows:
                    w.writerow({**row, "policy": r.policy, "budget": r.budget})

        meta = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "phi": self.phi,
            "budgets": self.budgets,
            "policies": self.policies,
            "scenarios": [s.id for s in self.scenarios],
            "cost_model": self.cost.describe(),
            "state_full_bytes": self.results[0].scenario_bytes if self.results else 0,
        }
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def markdown_table(summary_rows: list[dict[str, Any]]) -> str:
        cols = ["policy", "budget", "phi", "IR", "FAR", "CR", "cost_total",
                "fork_count", "dryrun_count", "budget_exhausted", "avg_fork_ms"]
        head = "| " + " | ".join(cols) + " |"
        sep = "|" + "|".join(["---"] * len(cols)) + "|"
        lines = [head, sep]
        for r in summary_rows:
            lines.append("| " + " | ".join(str(r.get(c)) for c in cols) + " |")
        return "\n".join(lines)
