"""循环层:harness 的核心状态机。

模型只会"说"(生成 token)。这一层的全部工作,是决定它"说完之后发生什么":

    拼上下文 -> 调模型 -> 有 tool_calls?
                            ├─ 没有 -> 这就是最终答复,结束
                            └─ 有   -> 执行 -> 把结果作为 role=tool 消息追加 -> 再调模型

外加三件让循环"能跑完"的事:上下文预算、轮数上限、错误回填。
"""
from __future__ import annotations

import json
from typing import Callable

from llm import ChatClient, LLMError
from tools import ToolRegistry

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
    def __init__(
        self,
        client: ChatClient,
        tools: ToolRegistry,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_turns: int = 20,
        context_chars: int = 20000,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.client = client
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.context_chars = context_chars
        self.on_event = on_event

    def run(self, task: str) -> str:
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt.format(workspace=self.tools.workspace)},
            {"role": "user", "content": task},
        ]
        last_text = ""

        for turn in range(1, self.max_turns + 1):
            messages = fit_context(messages, self.context_chars)
            self._emit("request", turn=turn, messages=messages)

            try:
                comp = self.client.chat(messages, tools=self.tools.schemas())
            except LLMError as exc:
                return f"[harness] 模型调用失败:{exc}"

            self._emit(
                "response", turn=turn, content=comp.content, usage=comp.usage,
                tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in comp.tool_calls],
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
                    return (comp.content + "\n[harness] 输出达到 max_tokens 上限,可能被截断。").strip()
                return comp.content or "(模型没有返回内容)"

            for call in comp.tool_calls:
                result = self.tools.call(call.name, call.arguments)
                self._emit(
                    "tool", turn=turn, name=call.name,
                    arguments=call.arguments, result=result,
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        return f"[harness] 达到最大轮数 {self.max_turns},任务未完成。最后输出:\n{last_text}"

    def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event({"kind": kind, **data})


def fit_context(messages: list[dict], budget_chars: int) -> list[dict]:
    """按字符预算裁剪上下文。

    保留 system 和第一条 user(任务目标),从最旧的一"轮"开始丢。
    以 assistant 为界整段丢弃(连同其后的 tool 消息),避免留下孤立的
    tool_call_id —— 那会被模型服务拒绝。
    """
    if _size(messages) <= budget_chars or len(messages) <= 3:
        return messages

    head, rest = messages[0], messages[1:]
    task = rest[0] if rest and rest[0].get("role") == "user" else None
    body = rest[1:] if task else rest

    while body and _size([head] + ([task] if task else []) + body) > budget_chars:
        if body[0].get("role") == "assistant":
            i = 1
            while i < len(body) and body[i].get("role") == "tool":
                i += 1
            body = body[i:]
        else:
            body = body[1:]

    return [head] + ([task] if task else []) + body


def _size(messages: list[dict]) -> int:
    return len(json.dumps(messages, ensure_ascii=False))
