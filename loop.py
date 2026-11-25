"""循环层：harness 的核心状态机。

模型只会「说」（生成 token）。这一层的全部工作，是决定它「说完之后发生什么」：

    拼上下文 → 调模型 → 有 tool_calls?
                          ├─ 没有 → 这就是最终答复，结束
                          └─ 有   → 执行 → 结果作为 role=tool 追加 → 再调模型

外加三件让循环「能跑完」的事：上下文预算、轮数上限、错误回填。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Callable

from llm import ChatClient, LLMError
from tools import ToolRegistry

__all__ = ["Agent", "SYSTEM_PROMPT", "count_tokens", "fit_context"]

SYSTEM_PROMPT = """你是运行在用户本地机器上的编码 agent,通过工具与文件系统、shell 交互。

工作区根目录(所有相对路径都相对于它):{workspace}

工作方式:
- 事实(文件内容、目录结构、命令输出)必须用工具确认,不要凭空猜测。
- 可以一次发起多个工具调用,它们会按顺序执行。
- 修改文件前先 read_file;write_file 会整文件覆盖。
- 工具返回 [error] 时,先读懂错误再换做法,不要重复同一个失败调用。
- 任务完成、或无需工具时,直接用文字给出最终答复,并且不要再调用工具。
- 用用户的语言回答,简洁,先给结论。
"""


class Agent:
    """一次对话的完整状态：历史、预算、轮数上限、事件回调。"""

    def __init__(
        self,
        client: ChatClient,
        tools: ToolRegistry,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_turns: int = 20,
        context_tokens: int = 7000,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.context_tokens = context_tokens
        self.on_event = on_event

    def run(self, task: str, on_text: Callable[[str], None]) -> str:
        """执行一个任务，返回最终答复（或失败说明）。

        始终走流式：模型生成的文本片段会实时回调给 ``on_text``。
        """
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt.format(workspace=self.tools.workspace)},
            {"role": "user", "content": task},
        ]
        last_text = ""

        for turn in range(1, self.max_turns + 1):
            before_tokens = count_tokens(messages)
            fitted = fit_context(messages, self.context_tokens)
            if len(fitted) != len(messages):
                self._emit(
                    "context", turn=turn,
                    before_tokens=before_tokens,
                    after_tokens=count_tokens(fitted),
                    dropped=len(messages) - len(fitted),
                )
            messages = fitted
            self._emit("request", turn=turn, messages=messages)

            try:
                comp = self.client.chat(
                    messages,
                    tools=self.tools.schemas(),
                    stream=True,
                    on_text=on_text,
                )
            except LLMError as exc:
                return f"[harness] 模型调用失败:{exc}"

            self._emit(
                "response", turn=turn, content=comp.content, usage=comp.usage,
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
            messages.append(assistant)

            if not comp.tool_calls:
                if comp.finish_reason == "length":
                    return (
                        comp.content
                        + "\n[harness] 输出达到 max_tokens 上限,可能被截断。"
                    ).strip()
                return comp.content or "(模型没有返回内容)"

            for call in comp.tool_calls:
                result = self.tools.call(call.name, call.arguments)
                self._emit(
                    "tool", turn=turn, name=call.name,
                    arguments=call.arguments, result=result,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

        return f"[harness] 达到最大轮数 {self.max_turns},任务未完成。最后输出:\n{last_text}"

    def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event({"kind": kind, **data})


# ---------------------------------------------------------------------------
# token 计数：tiktoken 有就用，没有就退化成字符估算
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _encoder() -> Any | None:
    """返回 tiktoken 编码器；不可用时返回 None（只尝试一次）。

    默认**不启用**：`encoding_for_model()` 在词表缺失时会联网下载，
    网络不通就会卡住（实测首次调用可能超过 60 秒）。
    需要精确计数时显式开启：

        pip install tiktoken
        export MINI_HARNESS_TOKENIZER=on
    """
    mode = os.environ.get("MINI_HARNESS_TOKENIZER", "off").strip().lower()
    if mode not in ("1", "on", "true", "auto"):
        return None
    try:
        import tiktoken  # 可选依赖
    except ImportError:
        return None
    try:
        return tiktoken.encoding_for_model("gpt-4")
    except Exception:  # 词表下载失败、版本不兼容等
        return None


def _text_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return max(1, len(text) // 2)  # 兜底：2 字符 ≈ 1 token
    return len(enc.encode(text))


def count_tokens(messages: list[dict]) -> int:
    """按 OpenAI messages 的官方口径估算一组消息的 token 数。"""
    total = 0
    for msg in messages:
        total += 4  # 每条消息的协议开销
        for key, value in msg.items():
            total += _text_tokens(str(value))
            if key == "name":
                total -= 1
    return total + 2  # 结尾 assistant 前缀的固定开销


def _truncate_message(message: dict, limit_tokens: int) -> dict:
    """把单条超长消息截到 ``limit_tokens`` 以内（返回新 dict）。"""
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message
    used = _text_tokens(content)
    if used <= limit_tokens:
        return message
    keep = max(200, int(len(content) * limit_tokens / used))
    clipped = dict(message)
    clipped["content"] = content[:keep] + "\n...[单条消息过长,已截断]"
    return clipped


def fit_context(messages: list[dict], budget_tokens: int) -> list[dict]:
    """按 token 预算裁剪上下文。

    策略（顺序很重要）：
    1. 装得下就原样返回 —— 大多数任务在 1M 窗口下根本不会触发裁剪；
    2. 单条消息超过预算一半时先截断（否则"一条 50KB 的 read_file 结果"
       会让后面的轮次全丢光也装不下）；
    3. 仍超预算，就从最旧的一「轮」开始整段丢（assistant + 其后的 tool），
       绝不留孤立的 ``tool_call_id`` —— 那会被模型服务拒绝（400）。

    始终保留 system 和第一条 user（任务目标）。
    """
    if count_tokens(messages) <= budget_tokens or len(messages) <= 3:
        return messages

    head, rest = messages[0], messages[1:]
    task = rest[0] if rest and rest[0].get("role") == "user" else None
    body = rest[1:] if task else rest
    kept = [head] + ([task] if task else [])

    # 2) 单条封顶：预算的一半
    per_message_cap = max(256, budget_tokens // 2)
    body = [_truncate_message(m, per_message_cap) for m in body]

    # 3) 按轮丢：先算好每条大小，丢弃时做减法，避免反复编码
    sizes = [count_tokens([m]) for m in body]
    total = count_tokens(kept) + sum(sizes)
    i = 0
    while i < len(body) and total > budget_tokens:
        j = i + 1
        if body[i].get("role") == "assistant":
            while j < len(body) and body[j].get("role") == "tool":
                j += 1
        total -= sum(sizes[i:j])
        i = j

    return kept + body[i:]