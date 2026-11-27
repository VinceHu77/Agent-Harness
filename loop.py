"""循环层：harness 的核心状态机。

模型只会「说」（生成 token）。这一层的全部工作，是决定它「说完之后发生什么」：

    拼上下文 → 调模型 → 有 tool_calls?
                          ├─ 没有 → 这就是最终答复，结束
                          └─ 有   → 执行 → 结果作为 role=tool 追加 → 再调模型

外加三件让循环「能跑完」的事：预算告警、轮数上限、错误回填。

**不再做 token 计算。** 上下文大小以服务端返回的 ``usage.prompt_tokens`` 为准，
超过 ``context_tokens`` 只发告警、不裁剪——计算交给计费方，决策留给人。
"""

from __future__ import annotations

from typing import Callable

from gate import decide
from llm import ChatClient, LLMError
from tools import ToolRegistry

__all__ = ["Agent", "SYSTEM_PROMPT"]

SYSTEM_PROMPT = """你是电商客服助理，通过工具查询订单和客户信息，帮助用户解决问题。

工作方式：
- 订单号、金额、退款记录这类事实，必须先调用工具确认，绝不凭印象编造。
- 一次可以发起多个工具调用，它们会按顺序执行。
- 工具返回 [error] 时，先读懂错误再换做法，不要重复同一个失败调用。
- 退款、发券、扣积分、关单属于不可逆操作：先说明将要发生什么，确认无误再执行。
- 超出政策（超额、重复、跨租户）或需要例外处理时，拒绝并转人工，不要自己拍板。
- 任务完成、或无需工具时，直接用文字给出最终答复，并且不要再调用工具。
- 用用户的语言回答，简洁，先给结论。
"""


class Agent:
    """一次对话的完整状态：历史、预算、轮数上限、事件回调。"""

    def __init__(
        self,
        client: ChatClient,
        tools: ToolRegistry,
        *,
        on_text: Callable[[str], None],
        system_prompt: str = SYSTEM_PROMPT,
        max_turns: int = 20,
        context_tokens: int = 7000,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.on_text = on_text
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.context_tokens = context_tokens
        self.on_event = on_event
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self.last_usage: dict = {}          # 最近一次请求的 usage（服务端真实值）
        self.usage_total: dict = {}         # 本会话累计（跨 run 累加）

    def reset(self) -> None:
        """清空对话历史（保留 system prompt）。"""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def run(self, task: str) -> str:
        """在**已有历史**上追加一轮，返回最终答复（或失败说明）。

        历史由 ``self.messages`` 持有：REPL 里连续两句话是同一段对话，
        所以「先说确认，再回一句确认」才能成立。``reset()`` 清空。
        """
        self.messages.append({"role": "user", "content": task})
        last_text = ""

        for turn in range(1, self.max_turns + 1):
            self._warn_if_over_budget(turn)
            self._emit("request", turn=turn, messages=self.messages)

            try:
                comp = self.client.chat(
                    self.messages,
                    tools=self.tools.schemas(),
                    stream=True,
                    on_text=self.on_text,
                )
            except LLMError as exc:
                return f"[harness] 模型调用失败:{exc}"

            self.last_usage = comp.usage or {}
            self._accumulate(self.last_usage)

            self._emit(
                "response", turn=turn, content=comp.content, usage=self.last_usage,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in comp.tool_calls
                ],
            )
            last_text = comp.content

            assistant: dict = {"role": "assistant", "content": comp.content}
            if comp.tool_calls:
                assistant["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in comp.tool_calls
                ]
            self.messages.append(assistant)

            if not comp.tool_calls:
                if comp.finish_reason == "length":
                    return (
                        comp.content
                        + "\n[harness] 输出达到 max_tokens 上限,可能被截断。"
                    ).strip()
                return comp.content or "(模型没有返回内容)"

            for call in comp.tool_calls:
                decision = decide(call.name, call.arguments)
                if decision["decision"] == "deny":
                    result = (f"[error] 已拒绝（{decision['rule']}）："
                              f"{decision['reason']}")
                else:
                    result = self.tools.call(call.name, call.arguments)
                self._emit(
                    "tool", turn=turn, name=call.name,
                    arguments=call.arguments, result=result,
                    decision=decision["decision"], rule=decision["rule"],
                )
                self.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

        return f"[harness] 达到最大轮数 {self.max_turns},任务未完成。最后输出:\n{last_text}"

    def _warn_if_over_budget(self, turn: int) -> None:
        """超过预算只告警、不裁剪。

        依据是**上一轮服务端返回的 prompt_tokens**（真实值）。
        第一轮没有上一轮数据，所以裸发，不做任何猜测。
        """
        used = self.last_usage.get("prompt_tokens", 0)
        if used and used > self.context_tokens:
            self._emit(
                "context", turn=turn, over_budget=True,
                prompt_tokens=used, budget=self.context_tokens,
            )

    def _accumulate(self, usage: dict) -> None:
        """把一次请求的 usage 累加进本会话总量。"""
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self.usage_total[key] = self.usage_total.get(key, 0) + value

    def usage_report(self) -> str:
        """一行汇总本会话累计 token（全部来自服务端 usage）。"""
        if not self.usage_total:
            return "(尚无 usage 数据)"
        u = self.usage_total
        return (
            f"prompt={u.get('prompt_tokens', 0)} "
            f"completion={u.get('completion_tokens', 0)} "
            f"total={u.get('total_tokens', 0)} "
            f"cache_hit={u.get('prompt_cache_hit_tokens', 0)}"
        )

    def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event({"kind": kind, **data})
