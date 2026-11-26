"""工具层：注册表 + 客服工具装载。

两个职责：
1. 给模型看：``schemas()`` 生成 JSON Schema，随每次请求发给模型。
2. 替模型做：``call()`` 执行工具，把结果（**包括错误**）变成字符串回填。

所有异常都在 ``call()`` 里被转成 ``[error] ...`` 字符串，而不是抛出去。
这是"模型能自纠"的前提：一次失败不应该结束整个会话。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

__all__ = ["MAX_OUTPUT", "Tool", "ToolRegistry", "clip"]

# 单个工具结果回填给模型的最大字符数，防止一条输出撑爆上下文
MAX_OUTPUT = 8000


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    """截断过长的工具输出，并告诉模型原文有多长。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[截断,原长 {len(text)} 字符]"


@dataclass(frozen=True)
class Tool:
    """一个可被模型调用的工具。"""

    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]


class ToolRegistry:
    """工具集合。内置工具由 cstools 注册，测试/扩展可以用 ``add()`` 追加。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._register_cs_tools()

    # ---- 给模型看的接口 ----

    def schemas(self) -> list[dict]:
        """OpenAI 格式的 tools 数组。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        """已注册的工具名。"""
        return list(self._tools)

    # ---- 注册 ----

    def add(self, tool: Tool) -> None:
        """按名字覆盖式注册。"""
        self._tools[tool.name] = tool

    def _register_cs_tools(self) -> None:
        from cstools import register_cs_tools  # 局部 import，避免循环依赖
        register_cs_tools(self)

    # ---- 执行 ----

    def call(self, name: str, raw_args) -> str:
        """执行一次工具调用。永远返回字符串，不抛异常。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"[error] 未知工具 {name!r}。可用工具: {', '.join(self._tools)}"

        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                return f"[error] 参数不是合法 JSON: {exc}。收到: {raw_args[:300]}"
        else:
            args = dict(raw_args or {})

        if not isinstance(args, dict):
            return f"[error] 参数必须是 JSON 对象,收到: {type(args).__name__}"

        try:
            result = tool.fn(**args)
        except TypeError as exc:            # 参数名/数量不对
            return f"[error] 参数不匹配: {exc}"
        except Exception as exc:            # 工具内部错误也要回填，模型才可能自纠
            return f"[error] {type(exc).__name__}: {exc}"

        return clip(str(result), MAX_OUTPUT)
