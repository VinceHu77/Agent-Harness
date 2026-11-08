"""动作（Action）与工具能力标签（ToolSpec）。

设计要点（对应开题报告 3.2 / 4.2）：
  * 风险档 R0–R3 由**工具能力标签**决定，而不是由 PreVal 的分数决定。
    这是把否决权从概率评分里剥离出来的第一步：不可逆动作必然落在 R2/R3。
  * `irreversible` / `dryrun_ok` 是独立于 tier 的显式字段，避免把
    「风险高低」和「能否安全试探」混为一谈。
  * `state_slots` 声明该工具会触碰哪些状态槽位，供后续（P1）做
    副作用感知的「最小快照范围」推导；P0 先把它作为元数据落库。

风险档语义：
  R0 无副作用（读、检索）
  R1 副作用可逆（打标签、备注、状态流转）
  R2 不可逆但可预演（退款执行、优惠券核销）——dry-run 优先
  R3 不可逆且不可预演（批量外发、销户、监管升级）——fork / 人工

与开题报告 4.4 表的对齐说明：
  4.4 把「退款预校验 / 优惠券预检」列在 R2 行，但这两个工具本身是**只读探针**
  （调用它不产生副作用）。本实现把它们标为 R0，并把 dry-run 能力记录在
  真正的高风险工具（refund_execute / coupon_redeem）的 `dryrun_ok` 上。
  这样 R2 的语义才与 4.3「不可逆但可预演」一致。这一点需要在论文中显式说明。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Tier(IntEnum):
    """风险档。数值越大越危险，便于比较。"""

    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3

    @property
    def label(self) -> str:
        return f"R{int(self)}"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    irreversible: bool
    dryrun_ok: bool
    state_slots: tuple[str, ...]
    summary: str


TOOL_SPECS: dict[str, ToolSpec] = {
    # ---- R0：无副作用 ----------------------------------------------------
    "order_query": ToolSpec("order_query", Tier.R0, False, False, (), "订单查询（只读）"),
    "logistics_query": ToolSpec("logistics_query", Tier.R0, False, False, (), "物流查询（只读）"),
    "faq_search": ToolSpec("faq_search", Tier.R0, False, False, (), "FAQ / 政策检索（只读）"),
    "refund_preview": ToolSpec("refund_preview", Tier.R0, False, True, (), "退款预演探针（模拟网关 preview）"),
    "coupon_preview": ToolSpec("coupon_preview", Tier.R0, False, True, (), "优惠券核销预检（只读）"),
    # ---- R1：可逆副作用 --------------------------------------------------
    "ticket_tag": ToolSpec("ticket_tag", Tier.R1, False, False, ("ticket",), "工单打标签"),
    "session_note": ToolSpec("session_note", Tier.R1, False, False, ("ticket",), "会话备注"),
    "ticket_status": ToolSpec("ticket_status", Tier.R1, False, False, ("ticket",), "工单状态流转"),
    # ---- R2：不可逆但可预演 ----------------------------------------------
    "refund_execute": ToolSpec(
        "refund_execute", Tier.R2, True, True, ("order", "ledger"), "执行退款（真实资金流出）"
    ),
    "coupon_redeem": ToolSpec(
        "coupon_redeem", Tier.R2, True, True, ("coupon", "order"), "优惠券核销（一次性）"
    ),
    # ---- R3：不可逆且不可预演 --------------------------------------------
    "bulk_message": ToolSpec(
        "bulk_message", Tier.R3, True, False, ("customer", "outbox"), "向客户批量外发消息"
    ),
    "account_close": ToolSpec(
        "account_close", Tier.R3, True, False, ("customer", "points"), "销户 / 积分清零"
    ),
    "escalate_regulator": ToolSpec(
        "escalate_regulator", Tier.R3, True, False, ("ticket", "external"), "工单升级至监管渠道"
    ),
}


def spec_for(tool: str) -> ToolSpec:
    try:
        return TOOL_SPECS[tool]
    except KeyError as exc:  # 未知工具必须显式失败，绝不静默放行
        raise ValueError(f"未注册的工具：{tool!r}（未知工具=true 副作用=不可控）") from exc


@dataclass
class Action:
    """一次待判定的工具调用请求。

    request_id 是贯穿账本、fork 分支、dry-run 抽样的一致性标识。
    """

    request_id: str
    session_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    namespace: str = "ns_A"
    user_text: str = ""
    branch_id: str = "main"

    @property
    def spec(self) -> ToolSpec:
        return spec_for(self.tool)

    @property
    def tier(self) -> Tier:
        return self.spec.tier

    @property
    def irreversible(self) -> bool:
        return self.spec.irreversible

    def canonical_args(self) -> str:
        return json.dumps(self.args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @property
    def arg_digest(self) -> str:
        return hashlib.sha256(self.canonical_args().encode("utf-8")).hexdigest()[:16]

    @property
    def idem_key(self) -> str:
        """幂等键：同工具 + 同参数 → 同键。用于识别重复执行。"""
        return f"{self.tool}:{self.arg_digest}"

    def with_branch(self, branch_id: str) -> "Action":
        return Action(
            request_id=self.request_id,
            session_id=self.session_id,
            tool=self.tool,
            args=dict(self.args),
            namespace=self.namespace,
            user_text=self.user_text,
            branch_id=branch_id,
        )
