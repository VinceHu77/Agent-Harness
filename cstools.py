"""客服工具：读写 ``world.db``，每个函数返回给模型看的纯文本。

只读工具（order_query 等）直接查库；写工具会在后面的步骤里加，
并且都会先经过 ``gate`` 判定。
"""

from __future__ import annotations

import json

import world

__all__ = ["order_query", "register_cs_tools"]

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


def register_cs_tools(registry) -> None:
    """把客服工具注册进 ToolRegistry。"""
    from tools import Tool  # 局部 import：避免 tools ↔ cstools 循环依赖

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
