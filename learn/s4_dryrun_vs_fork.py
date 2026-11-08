#!/usr/bin/env python3
"""站 4：dry-run 与 fork —— 都「不碰真实世界」，但能力不同。

跑法：python learn/s4_dryrun_vs_fork.py

看完你应该能回答：
  * dry-run 和 fork 的区别到底是什么？（一个是预演判断，一个是分叉执行）
  * 为什么 fork 也救不了「已经发出去的副作用」（约束 C1）？
"""

from __future__ import annotations

from _kit import act, build, section, title

ORDER = "ORDER-1007"   # 面值 5000


def main() -> None:
    title("站 4：dry-run / fork / C1 —— 三件必须分清的事")

    rt = build(policy="OURS", budget=200, phi=0.6)

    # 先制造一个「已经退过 3000」的世界
    rt.world.apply(act("refund_execute", {"order_id": ORDER, "amount": 3000}, "seed"))
    print(f"\n前置：{ORDER} 已退 3000 / 5000。再退 3000 就会超额。")
    h_before = rt.world.state_hash()

    section("1. dry-run：在影子上执行，返回「有没有违反不变量」")
    danger, codes = rt.world.preview(
        act("refund_execute", {"order_id": ORDER, "amount": 3000}, "p1"), phi=1.0)
    print(f"  φ=1.0（完全可信）  danger={danger}  codes={sorted(codes)}")
    danger06, codes06 = rt.world.preview(
        act("refund_execute", {"order_id": ORDER, "amount": 3000}, "p2"), phi=0.6)
    print(f"  φ=0.6（会失真）    danger={danger06}  codes={sorted(codes06)}")
    print("  ↑ φ<1 时预演可能漏报 —— 这就是 C3，也是仍然需要 fork 的理由。")
    print(f"  预演之后世界没变？  {rt.world.state_hash() == h_before}")

    section("2. fork：状态分叉 + 影子世界 + 不变量比对，并给出成本")
    res = rt.fork.run(act("refund_execute", {"order_id": ORDER, "amount": 3000}, "f1"))
    print(f"  danger            {res.danger}")
    print(f"  违反的不变量      {list(res.codes)}")
    print(f"  状态分叉字节      {res.state_bytes} B   （只搬 delta）")
    print(f"  影子世界字节      {res.shadow_bytes} B  （整库复制，成本大头）")
    print(f"  fork 耗时         {res.elapsed_ms:.3f} ms")
    print(f"  成本              {res.cost:.2f} 单位")
    print(f"  真实世界没变？    {rt.world.state_hash() == h_before}")

    section("3. C1：fork 隔离状态，但隔离不了「已经发出的副作用」")
    print("  假设有个分支不听话，直接调了真实工具（这是架构禁止、但现实会发生的事）：")
    rt.world.apply(
        act("refund_execute", {"order_id": ORDER, "amount": 100}, "leak"),
        branch_id="branch-naughty")
    print(f"    世界哈希            {h_before} → {rt.world.state_hash()}")
    print(f"    退款流水条数        {rt.world.conn.execute('SELECT COUNT(*) FROM refunds').fetchone()[0]}")
    print("  接着把分支状态整个丢掉（等价于 rollback 状态）：")
    del rt.state
    print(f"    世界哈希还是变过的？{rt.world.state_hash() != h_before}")
    print("  → 状态可以回滚，外部副作用不能。所以拦必须在「发出之前」。")

    section("4. 一句话记住三者")
    print("  dry-run = 预演『会不会出事』，便宜，但不一定准（φ）")
    print("  fork    = 在隔离副本里真跑一遍，准，但贵（整库影子）")
    print("  账本    = 已经发生的事，永远不能回滚，只能记下来")

    section("思考题")
    print("  Q1: 如果把 shadow_conn 换成「只复制 action.spec.state_slots 涉及的表」，")
    print("      shadow_bytes 会降到多少？这会影响 V_fork 的判定吗？")
    print("  Q2: 为什么 fork 的 state_bytes 是 30 B 级，而 shadow_bytes 是 84 KB 级？")


if __name__ == "__main__":
    main()
