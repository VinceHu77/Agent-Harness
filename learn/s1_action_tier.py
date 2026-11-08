#!/usr/bin/env python3
"""站 1：动作与风险档 —— 全系统的「名词表」。

跑法：python learn/s1_action_tier.py

看完你应该能回答：
  * 为什么风险档由工具决定，而不是由 PreVal 的分数决定？
  * 幂等键为什么要把「参数摘要」算进去？
  * 加一个新工具，最少要改哪两处？
"""

from __future__ import annotations

from _kit import title, section
from harness.core.action import TOOL_SPECS, Action, Tier, spec_for


def main() -> None:
    title("站 1：动作（Action）与工具能力标签（ToolSpec）")

    section("1. 13 个工具的风险档（源码在 harness/core/action.py 第 56 行）")
    print(f"{'工具':<18} {'档':<4} {'不可逆':<7} {'可预演':<7} 影响状态槽")
    for name, spec in TOOL_SPECS.items():
        slots = ",".join(spec.state_slots) or "-"
        print(f"{name:<18} {spec.tier.label:<4} {str(spec.irreversible):<7} "
              f"{str(spec.dryrun_ok):<7} {slots}")

    print("\n读法：R2 = 不可逆但可预演（能 dry-run 先试），"
          "R3 = 不可逆且不可预演（只能 fork）。")
    print("注意 refund_preview 是 R0：它自己只是只读探针，不产生副作用。")

    section("2. 一个动作长什么样")
    a = Action(
        request_id="req-001",
        session_id="session-1",
        tool="refund_execute",
        args={"order_id": "ORDER-1007", "amount": 3000},
        namespace="ns_A",
        user_text="客户情绪很激动，先退 3000 安抚一下。",
    )
    print(f"工具        {a.tool}")
    print(f"风险档      {a.tier.label}（Tier 是 IntEnum，可比较大小）")
    print(f"不可逆      {a.irreversible}")
    print(f"可预演      {a.spec.dryrun_ok}")
    print(f"影响状态槽  {a.spec.state_slots}")
    print(f"参数规范化  {a.canonical_args()}")
    print(f"参数摘要    {a.arg_digest}   ← sha256 前 16 位")
    print(f"幂等键      {a.idem_key}")

    section("3. 幂等键：同参数 → 同键；换分支 → 不变")
    b = Action(request_id="req-002", session_id="session-1", tool="refund_execute",
               args={"amount": 3000, "order_id": "ORDER-1007"})  # 参数顺序不同
    c = a.with_branch("fork:req-001")
    print(f"参数顺序不同，键相同？  {a.idem_key == b.idem_key}")
    print(f"换分支后键不变？        {a.idem_key == c.idem_key}")
    print(f"分支名                  {c.branch_id}")
    print("\n这就是账本能回答「这笔操作是不是重复执行」的原因：")
    print("键只由 工具 + 参数 决定，和「谁在哪个分支发起」无关。")

    section("4. 未知工具必须显式失败")
    try:
        spec_for("rm_rf")
    except ValueError as exc:
        print(f"抛异常：{exc}")
    print("宁可崩，也不要静默放行一个没人定义过副作用的工具。")

    section("5. 改动练习")
    print("加一个 R2 工具 data_export（导出客户数据），需要改两处：")
    print("  ① harness/core/action.py 的 TOOL_SPECS 里注册 ToolSpec")
    print("  ② harness/env/world.py 的 _apply() 里写执行逻辑")
    print("如果它会泄漏数据，还要在 world.py 的 violations() 里加一条不变量。")


if __name__ == "__main__":
    main()
