"""调度策略：基线 B1–B3、完整方案 Ours、消融 A1–A4。

所有策略实现同一个接口 `decide(action, ctx, score) -> Verdict`，
差别只在配置。这样在相同预算 B、相同场景、相同评分器噪声下比较，
差异只能归因于架构本身 —— 这是做消融实验的前提。

  记号            含义
  ---------------- ------------------------------------------------------
  B1             仅 PreVal（阈值放行/拒绝）
  B2              PreVal + 全量快照 fork（每个不可逆动作都 fork）
  B3              仅硬门控
  OURS            硬门控 + PreVal + 分级调度 + 副作用感知 delta 快照
  A1              OURS 去掉硬门控
  A2              OURS 去掉状态分类（改用全量快照）
  A3              OURS 去掉分级调度（固定：不可逆就 fork）
  A4              等价 B1（兜底基线）
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.action import Action
from ..core.verdict import Decision, Verdict


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    desc: str
    use_gate: bool = True
    use_preval: bool = True
    mode: str = "tiered"          # tiered | preval_only | gate_only | always_fork
    use_dryrun: bool = True
    use_fork: bool = True
    snapshot: str = "delta"       # delta | full
    l_units: float = 55.0         # 一次泄漏的损失权重 L（V_fork 用）
    tau_deny: float = 0.65
    fallback: str = "deny"        # 预算耗尽时的降级：deny | allow


POLICIES: dict[str, PolicyConfig] = {
    "B1": PolicyConfig("B1", "仅 PreVal", use_gate=False, mode="preval_only",
                       use_dryrun=False, use_fork=False),
    "B2": PolicyConfig("B2", "PreVal + 全量快照 fork", mode="always_fork",
                       use_dryrun=False, use_fork=True, snapshot="full"),
    "B3": PolicyConfig("B3", "仅硬门控", use_gate=True, use_preval=False, mode="gate_only",
                       use_dryrun=False, use_fork=False),
    "OURS": PolicyConfig("OURS", "三层 + 分级调度 + delta 快照"),
    "A1": PolicyConfig("A1", "去掉硬门控", use_gate=False),
    "A2": PolicyConfig("A2", "去掉状态分类（全量快照）", snapshot="full"),
    "A3": PolicyConfig("A3", "去掉分级调度（固定 fork）", mode="always_fork", use_dryrun=False),
    "A4": PolicyConfig("A4", "兜底基线（等价 B1）", use_gate=False, mode="preval_only",
                       use_dryrun=False, use_fork=False),
}


class Policy:
    def __init__(self, cfg: PolicyConfig) -> None:
        self.cfg = cfg

    @property
    def name(self) -> str:
        return self.cfg.name

    # ------------------------------------------------------------------
    def decide(self, action: Action, ctx, score: float | None) -> Verdict:
        cfg = self.cfg

        # 可逆动作不进入隔离决策（R0/R1）
        if not action.irreversible:
            return Verdict(Decision.ALLOW, "reversible", "policy", score=score,
                           cost=ctx.cost.allow)

        if cfg.use_preval and score is not None and score >= cfg.tau_deny:
            return Verdict(Decision.DENY, "preval_deny", "preval", score=score,
                           cost=ctx.cost.allow)

        if cfg.mode == "preval_only":
            return Verdict(Decision.ALLOW, "preval_pass", "preval", score=score,
                           cost=ctx.cost.allow)
        if cfg.mode == "gate_only":
            return Verdict(Decision.ALLOW, "gate_pass", "hard_gate", score=score,
                           cost=ctx.cost.allow)

        if cfg.mode == "always_fork":
            return self._fork(action, ctx, score, "fixed_fork")

        # ---------------- 分级调度（Ours 的核心） --------------------
        if action.spec.dryrun_ok and cfg.use_dryrun:
            return self._dryrun_then_maybe_fork(action, ctx, score)

        # 不可预演（R3）：无 dry-run 可用，直接 fork 取信息
        if cfg.use_fork:
            return self._fork(action, ctx, score, "no_dryrun")
        return self._fallback(action, ctx, score, "no_isolation")

    # ------------------------------------------------------------------
    def _dryrun_then_maybe_fork(self, action: Action, ctx, score: float | None) -> Verdict:
        cost = ctx.cost.dryrun
        if not ctx.budget.can(cost):
            return self._fallback(action, ctx, score, "budget_dryrun")
        ctx.budget.spend(cost, "dryrun")

        danger, codes = ctx.world.preview(action, ctx.phi)
        if danger:
            return Verdict(
                Decision.DENY, "dryrun_unsafe:" + ",".join(sorted(codes)), "dryrun",
                score=score, cost=cost, isolation="dryrun", checks=sorted(codes),
                extra={"dryrun_cost": cost},
            )

        # dry-run 说"安全"，但保真度 φ<1 时仍有残差风险。
        # V_fork = p_r · L · (1-φ) > C_fork  ← 开题报告 3.3 的触发条件
        gain = (score or 0.0) * self.cfg.l_units * (1.0 - ctx.phi)
        fork_cost = ctx.fork.estimate(action)
        if self.cfg.use_fork and gain > fork_cost:
            return self._fork(action, ctx, score, "residual_gain", pre_cost=cost,
                              gain=gain, c_fork=fork_cost)

        return Verdict(Decision.ALLOW, "dryrun_safe", "dryrun", score=score,
                       cost=cost, isolation="dryrun",
                       extra={"dryrun_cost": cost, "v_fork_gain": round(gain, 3),
                              "c_fork": round(fork_cost, 3)})

    # ------------------------------------------------------------------
    def _fork(self, action: Action, ctx, score: float | None, why: str,
              pre_cost: float = 0.0, gain: float | None = None,
              c_fork: float | None = None) -> Verdict:
        est = ctx.fork.estimate(action)
        if not ctx.budget.can(est):
            return self._fallback(action, ctx, score, "budget_fork")
        res = ctx.fork.run(action)
        ctx.budget.charge(res.cost, "fork")
        total = pre_cost + res.cost
        extra = {
            "fork_cost": round(res.cost, 3),
            "fork_state_bytes": res.state_bytes,
            "fork_shadow_bytes": res.shadow_bytes,
            "fork_ms": round(res.elapsed_ms, 3),
            "fork_why": why,
        }
        if gain is not None:
            extra["v_fork_gain"] = round(gain, 3)
        if c_fork is not None:
            extra["c_fork"] = round(c_fork, 3)
        if res.danger:
            return Verdict(
                Decision.DENY, "fork_blocked:" + ",".join(res.codes), "scheduler",
                score=score, cost=total, isolation="fork", checks=list(res.codes),
                extra=extra,
            )
        return Verdict(
            Decision.ALLOW, f"fork_verified({why})", "scheduler",
            score=score, cost=total, isolation="fork", extra=extra,
        )

    # ------------------------------------------------------------------
    def _fallback(self, action: Action, ctx, score: float | None, why: str) -> Verdict:
        ctx.budget.note_exhausted()
        if self.cfg.fallback == "allow":
            return Verdict(Decision.ALLOW, f"fallback_allow({why})", "budget", score=score,
                           cost=ctx.cost.allow)
        return Verdict(Decision.DENY, f"fallback_deny({why})", "budget", score=score,
                       cost=ctx.cost.allow)
