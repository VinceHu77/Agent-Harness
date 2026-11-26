"""客服工具：读写 ``world.db``，每个函数返回给模型看的纯文本。

只读工具（order_query 等）直接查库；写工具会在后面的步骤里加，
并且都会先经过 ``gate`` 判定。
"""

from __future__ import annotations

import json

import world

__all__ = ["order_query", "refund_apply", "refund_preview", "register_cs_tools", "ticket_create"]

STATUS_CN = {
    "paid": "已支付",
    "shipped": "已发货",
    "delivered": "已送达",
    "closed": "已关闭",
}


def order_query(order_id: str) -> str:
    """按订单号查询：状态、客户、实付/已退/剩余可退、商品。"""
    rows = world.query("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not rows:
        return f"[error] 订单不存在: {order_id}"

    order = rows[0]
    remaining = order["paid_cents"] - order["refunded_cents"]
    items = "、".join(
        f"{item['name']}×{item['qty']}" for item in json.loads(order["items_json"])
    )
    status = STATUS_CN.get(order["status"], order["status"])
    return (
        f"订单 {order['id']}（{status}）\n"
        f"客户: {order['customer_id']}\n"
        f"实付: ¥{order['paid_cents'] / 100:.2f}，"
        f"已退: ¥{order['refunded_cents'] / 100:.2f}，"
        f"剩余可退: ¥{remaining / 100:.2f}\n"
        f"商品: {items}"
    )


def ticket_create(customer_id: str, subject: str) -> str:
    """给客户开一张工单（可逆：写错可以关掉）。"""
    if not world.query("SELECT id FROM customers WHERE id = ?", (customer_id,)):
        return f"[error] 客户不存在: {customer_id}"

    subject = subject.strip()
    if not subject:
        return "[error] 工单主题不能为空"

    ticket_id = world.insert(
        "INSERT INTO tickets (customer_id, subject) VALUES (?, ?)",
        (customer_id, subject),
    )
    return f"已创建工单 #{ticket_id}（客户 {customer_id}，主题：{subject}）"


def refund_preview(order_id: str, amount_cents: int) -> str:
    """预览退款：只计算，不落库。"""
    rows = world.query("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not rows:
        return f"[error] 订单不存在: {order_id}"

    order = rows[0]
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError):
        return "[error] 退款金额必须是整数（单位：分）"
    if amount <= 0:
        return "[error] 退款金额必须大于 0"

    paid = order["paid_cents"]
    refunded = order["refunded_cents"]
    after = refunded + amount
    remaining_after = paid - after

    if remaining_after >= 0:
        tail = f"（剩余可退 ¥{remaining_after / 100:.2f}）"
    else:
        tail = f"（超出可退 ¥{-remaining_after / 100:.2f}）"

    lines = [
        f"[预览] 订单 {order['id']} 实付 ¥{paid / 100:.2f}，已退 ¥{refunded / 100:.2f}",
        f"本次退款 ¥{amount / 100:.2f} → 退款后累计 ¥{after / 100:.2f}{tail}",
    ]
    if after > paid:
        lines.append("[警告] 该退款超出可退金额，不可执行")
    return "\n".join(lines)


def refund_apply(order_id: str, amount_cents: int) -> str:
    """真正执行退款。是否允许由 ``gate.decide()`` 先判定。"""
    rows = world.query("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not rows:
        return f"[error] 订单不存在: {order_id}"

    order = rows[0]
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError):
        return "[error] 退款金额必须是整数（单位：分）"
    if amount <= 0:
        return "[error] 退款金额必须大于 0"

    remaining = order["paid_cents"] - order["refunded_cents"]
    if amount > remaining:
        return (f"[error] 退款超出可退金额：本次 ¥{amount / 100:.2f}，"
                f"剩余可退 ¥{remaining / 100:.2f}")

    updated = world.execute(
        "UPDATE orders SET refunded_cents = refunded_cents + ? "
        "WHERE id = ? AND refunded_cents + ? <= paid_cents",
        (amount, order_id, amount),
    )
    if updated == 0:
        return "[error] 退款失败：订单状态已变化，请重新预览"

    after = order["refunded_cents"] + amount
    return (
        f"已退款 ¥{amount / 100:.2f}。订单 {order_id} 累计退款 ¥{after / 100:.2f}，"
        f"剩余可退 ¥{(order['paid_cents'] - after) / 100:.2f}。"
    )


def register_cs_tools(registry) -> None:
    """把客服工具注册进 ToolRegistry。"""
    from tools import Tool  # 局部 import：避免 tools ↔ cstools 循环依赖

    registry.add(Tool(
        "ticket_create",
        "Create a support ticket for a customer. Use it when the issue cannot be "
        "resolved in the conversation. Returns the new ticket ID.",
        {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer ID, e.g. CUST-01",
                },
                "subject": {
                    "type": "string",
                    "description": "Short ticket subject",
                },
            },
            "required": ["customer_id", "subject"],
        },
        ticket_create,
    ))

    registry.add(Tool(
        "refund_preview",
        "Preview a refund WITHOUT executing it. Returns the order's paid, refunded "
        "and remaining amounts plus the effect of this refund. Always call this "
        "before telling the customer what will happen.",
        {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order ID, e.g. ORD-1001",
                },
                "amount_cents": {
                    "type": "integer",
                    "description": "Refund amount in cents, e.g. 10000 means ¥100.00",
                },
            },
            "required": ["order_id", "amount_cents"],
        },
        refund_preview,
    ))

    registry.add(Tool(
        "refund_apply",
        "Execute a refund. Only works when the harness was started with refunds "
        "enabled. Always call refund_preview first and get customer confirmation "
        "before calling this.",
        {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order ID, e.g. ORD-1001",
                },
                "amount_cents": {
                    "type": "integer",
                    "description": "Refund amount in cents, e.g. 10000 means ¥100.00",
                },
            },
            "required": ["order_id", "amount_cents"],
        },
        refund_apply,
    ))

    registry.add(Tool(
        "order_query",
        "Query an order by its ID. Returns status, customer, paid amount, "
        "refunded amount, remaining refundable amount, and items.",
        {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order ID, e.g. ORD-1001",
                },
            },
            "required": ["order_id"],
        },
        order_query,
    ))
