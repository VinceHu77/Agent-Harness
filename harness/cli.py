"""命令行入口。

用法：
  # 默认矩阵：B1/B2/B3/OURS × 预算 100/300/900
  python -m harness.cli run

  # 完整消融 + φ 敏感性
  python -m harness.cli run --policies B1,B2,B3,OURS,A1,A2,A3,A4 \
      --budgets 100,300,900 --phi 0.6 --out runs/p0_full
  python -m harness.cli run --phi 0.95 --out runs/p0_phi95

  # 只跑一条、看决策轨迹
  python -m harness.cli trace --policy OURS --budget 300
"""

from __future__ import annotations

import argparse
import sys

from .bench.loader import load_scenarios
from .bench.runner import Runner
from .core.cost import CostModel


def _policies(s: str) -> list[str]:
    return [x.strip().upper() for x in s.split(",") if x.strip()]


def _budgets(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def cmd_run(args: argparse.Namespace) -> int:
    policies = _policies(args.policies)
    runner = Runner(
        policies=policies,
        budgets=_budgets(args.budgets),
        phi=args.phi,
        cost_model=CostModel(per_mb=args.per_mb, fork_base=args.fork_base),
        l_units=args.l_units,
    )
    runner.run()
    out = runner.write(args.out)
    rows = [r.summary for r in runner.results]

    print(Runner.markdown_table(rows))
    print(f"\n产物目录：{out.resolve()}")
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    runner = Runner(policies=[args.policy], budgets=[args.budget], phi=args.phi)
    res = runner.run_one(args.policy, args.budget)
    print(f"策略 {args.policy}  预算 {args.budget}  φ {args.phi}\n")
    for row in res.ledger_rows:
        flag = "EXEC" if row["executed"] else "----"
        print(f"{row['scenario_id']:<8} {row['tool']:<20} {row['decision']:<6} "
              f"{flag}  score={row['risk_score']:<6} cost={row['cost']:<7} "
              f"src={row['source']:<9} {row['reason']}")
    print("\n汇总：")
    for k in ("IR", "FAR", "CR", "cost_total", "fork_count", "dryrun_count",
              "budget_exhausted", "avg_fork_ms"):
        print(f"  {k:<18} {res.summary[k]}")
    return 0


def cmd_scenarios(args: argparse.Namespace) -> int:
    for sc in load_scenarios():
        steps = ", ".join(
            f"{s.tool}{'!' if s.dangerous else ''}{'*' if s.required else ''}" for s in sc.steps
        )
        print(f"{sc.id:<8} {sc.kind:<7} {sc.attack_type:<32} {steps}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness", description="副作用感知快照 + 风险分级调度")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="跑策略矩阵")
    pr.add_argument("--policies", default="B1,B2,B3,OURS")
    pr.add_argument("--budgets", default="100,300,900")
    pr.add_argument("--phi", type=float, default=0.6)
    pr.add_argument("--per-mb", type=float, default=100.0)
    pr.add_argument("--fork-base", type=float, default=2.0)
    pr.add_argument("--l-units", type=float, default=55.0)
    pr.add_argument("--out", default="runs/p0")
    pr.set_defaults(func=cmd_run)

    pt = sub.add_parser("trace", help="打印单个策略的决策轨迹")
    pt.add_argument("--policy", default="OURS")
    pt.add_argument("--budget", type=float, default=300.0)
    pt.add_argument("--phi", type=float, default=0.6)
    pt.set_defaults(func=cmd_trace)

    ps = sub.add_parser("scenarios", help="列出场景集")
    ps.set_defaults(func=cmd_scenarios)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
