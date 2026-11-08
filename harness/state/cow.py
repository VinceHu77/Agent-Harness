"""写时复制（Copy-on-Write）的 Agent 状态。

对应开题报告 4.2 的两类可回滚状态：
  * 可增量快照：只把本分支的 delta 叠加上去（默认模式 `delta`）
  * COW 共享：子分支只读共享父层对象，写入时才复制一层

对照实验里 A2/B2 用 `full` 模式（每个分支复制全部状态），
Ours 用 `delta` 模式。两者的字节数差异就是 G1 要量化的「快照成本」。

注意：这里隔离的是**Agent 状态**，永远不包含已经发出的外部副作用。
后者只存在于 World 里，且不参与任何回滚 —— 这是 C1 的实现约束。
"""

from __future__ import annotations

import copy
import json
from typing import Any


class COWState:
    def __init__(self, local: dict[str, Any] | None = None, parent: "COWState | None" = None,
                 label: str = "main") -> None:
        self._local: dict[str, Any] = dict(local or {})
        self._parent = parent
        self.label = label
        self.writes = 0

    # --------------------------------------------------------------- 读写
    def get(self, key: str, default: Any = None) -> Any:
        node: COWState | None = self
        while node is not None:
            if key in node._local:
                return node._local[key]
            node = node._parent
        return default

    def set(self, key: str, value: Any) -> None:
        self._local[key] = value
        self.writes += 1

    # --------------------------------------------------------------- 分叉
    def fork(self, label: str = "fork") -> "COWState":
        """子分支共享父层，只有自己的写入是新数据。"""
        return COWState(parent=self, label=label)

    def materialize(self) -> dict[str, Any]:
        chain: list[COWState] = []
        node: COWState | None = self
        while node is not None:
            chain.append(node)
            node = node._parent
        out: dict[str, Any] = {}
        for node in reversed(chain):
            out.update(node._local)
        return out

    def clone_full(self) -> "COWState":
        """全量复制（A2 用）：模拟「无差别全量快照」。"""
        snap = COWState(local=copy.deepcopy(self.materialize()), label="full-snapshot")
        return snap

    # --------------------------------------------------------------- 度量
    def delta_bytes(self) -> int:
        """本分支自己新增/修改的字节数 —— 增量快照要搬的数据。"""
        return len(json.dumps(self._local, ensure_ascii=False, default=str).encode("utf-8"))

    def full_bytes(self) -> int:
        """全量快照要搬的数据。"""
        return len(json.dumps(self.materialize(), ensure_ascii=False, default=str).encode("utf-8"))

    def snapshot_bytes(self, mode: str = "delta") -> int:
        if mode == "full":
            return self.full_bytes()
        if mode == "delta":
            return self.delta_bytes()
        raise ValueError(f"未知快照模式：{mode}")


_BASE_CACHE: dict[str, Any] | None = None


def build_session_state(orders_hint: int = 200) -> COWState:
    """构造一个有真实体量的会话状态（约 400KB），让快照成本可被观测。

    内容模仿真实客服 Agent 的上下文：政策文档 + 订单缓存 + 会话历史 + 草稿。
    体积不是凑数：状态大小直接决定 fork 成本，是 RC2 的自变量。

    基座字典会被缓存复用（场景之间只做浅拷贝）：本实现只写顶层键，
    因此浅拷贝不会造成跨场景污染，但能让 400+ 次场景构造保持毫秒级。
    """
    global _BASE_CACHE
    if _BASE_CACHE is None:
        policy = ("【售后政策】退换货需在签收后 7 天内发起；单笔退款上限 5000 元；"
                  "超过 4000 元须主管审批；已退款订单不得重复退款；优惠券核销后退款"
                  "需扣除券面额；批量外发须取得客户明确同意；销户前须确认积分已兑付。") * 120
        order_cache = {
            f"ORDER-{2000 + i}": {
                "namespace": "ns_A" if i % 3 else "ns_B",
                "amount": float(100 + i * 7 % 4900),
                "status": "paid" if i % 5 else "refunded",
                "history": ["created", "paid", "shipped"] * 3,
                "note": "客户历史沟通记录占位，用于撑起真实上下文体积。" * 2,
            }
            for i in range(orders_hint)
        }
        conversation = [
            {"role": "user" if i % 2 == 0 else "assistant",
             "content": f"第 {i // 2} 轮会话记录：客户询问退款进度与物流状态，客服确认政策边界。" * 3}
            for i in range(120)
        ]
        _BASE_CACHE = {
            "policy_doc": policy,
            "order_cache": order_cache,
            "conversation": conversation,
            "tool_results": [],
            "draft": {},
            "turn": 0,
        }
    return COWState(local=dict(_BASE_CACHE), label="main")
