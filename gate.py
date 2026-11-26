"""门控：按风险层级和不变量决定一个工具调用能不能执行。

设计原则：
- **确定性**：只看工具名、参数和世界状态，不调模型；
- **fail-closed**：未知工具、参数缺失一律拒绝；
- **可解释**：每次拒绝都带一个规则编号和一句人话。

返回结构::

    {"decision": "allow" | "deny", "tier": "R0".."R3",
     "rule": "C-OVERREFUND", "reason": "..."}

``decision`` 只有 allow / deny 两档；dry-run（人工确认）是下一步的事。
"""

from __future__ import annotations

import json

import world

__all__ = ["TIERS", "decide"]

# 工具 → 风险层级。没列出来的工具视为 R3，默认拒绝。
TIERS: dict[str, str] = {
    "order_query": "R0",       # 纯读
    "refund_preview": "R0",    # 只算不写
    "ticket_create": "R1",     # 可逆写
    "refund_apply": "R2",      # 不可逆、影响金额
}


def _allow(tier: str) -> dict:
    return {"decision": "allow", "tier": tier, "rule": "", "reason": ""}


def _deny(tier: str, rule: str, reason: str) -> dict:
    return {"decision": "deny", "tier": tier, "rule": rule, "reason": reason}


def decide(tool_name: str, raw_args) -> dict:
    """判定一次工具调用。永远返回 dict，不抛异常。"""
    tier = TIERS.get(tool_name)
    if tier is None:
        return _deny("R3", "C-UNKNOWN-TOOL", f"未知工具 {tool_name!r}，默认拒绝")

    args, bad_json = _parse(raw_args)
    if bad_json:
        # 参数坏了不在这里拦：让 tools.call() 返回标准的"参数不是合法 JSON"
        return _allow(tier)

    if tool_name == "refund_apply":
        return _check_refund(args, tier)

    return _allow(tier)


def _parse(raw_args) -> tuple[dict, bool]:
    if isinstance(raw_args, dict):
        return raw_args, False
    if isinstance(raw_args, str):
        try:
            value = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            return {}, True
        return (value, False) if isinstance(value, dict) else ({}, True)
    return {}, True


def _check_refund(args: dict, tier: str) -> dict:
    order_id = args.get("order_id")
    if not order_id:
        return _deny(tier, "C-BAD-ARGS", "缺少 order_id")

    try:
        amount = int(args.get("amount_cents"))
    except (TypeError, ValueError):
        return _deny(tier, "C-BAD-AMOUNT", "amount_cents 必须是整数（单位：分）")
    if amount <= 0:
        return _deny(tier, "C-BAD-AMOUNT", "退款金额必须大于 0")

    rows = world.query("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not rows:
        return _deny(tier, "C-NOT-FOUND", f"订单不存在: {order_id}")

    order = rows[0]
    customers = world.query(
        "SELECT status FROM customers WHERE id = ?", (order["customer_id"],))
    if customers and customers[0]["status"] == "closed":
        return _deny(tier, "C-CLOSED", f"客户 {order['customer_id']} 已注销，不能退款")

    if order["status"] == "closed":
        return _deny(tier, "C-CLOSED", f"订单 {order_id} 已关闭，不能退款")

    remaining = order["paid_cents"] - order["refunded_cents"]
    if amount > remaining:
        return _deny(tier, "C-OVERREFUND",
                     f"退款 ¥{amount / 100:.2f} 超过剩余可退 ¥{remaining / 100:.2f}")

    return _allow(tier)
