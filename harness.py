#!/usr/bin/env python3
"""Mini Harness —— 最小但完整的 agent harness。

    python3 harness.py "任务描述"        # 一次性执行
    python3 harness.py                    # 交互式 REPL

默认连接本机 llama-server（OpenAI 兼容接口）：
    http://127.0.0.1:8080/v1 , model = qwen-local

连 DeepSeek 时从环境变量读 Key（不写进代码）：
    export DEEPSEEK_API_KEY=sk-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from llm import API_KEY_ENV, ChatClient
from loop import Agent
from tools import ToolRegistry

__version__ = "0.2.0"

DEFAULT_BASE_URL = os.environ.get("LLAMA_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("LLAMA_MODEL", "deepseek-flash")
DEFAULT_MAX_TURNS = 20          # 每轮都重发上下文，轮数越多越贵
DEFAULT_CONTEXT_TOKENS = 64_000  # deepseek-flash 窗口 1M；这里按常见任务给足余量
DEFAULT_TEMPERATURE = 0.0        # 工具调用要可复现
DEFAULT_MAX_TOKENS = 8192        # 单轮输出上限（模型上限 384K，够用即可）
DEFAULT_TRACE_DIR = Path("runs")


def one_line(text: str, n: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


class Tracer:
    def __init__(self, trace_path: Path | None) -> None:
        self.trace_path = trace_path

    def __call__(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "request":
            print(f"\n── 第 {event['turn']} 轮 ──")
        elif kind == "response":
            for call in event["tool_calls"]:
                print(f"  → {call['name']}({one_line(call['arguments'], 160)})")
        elif kind == "tool":
            print(f"  ← {one_line(event['result'], 400)}")

        if not self.trace_path:
            return
        line = json.dumps({"t": time.strftime("%H:%M:%S"), **event}, ensure_ascii=False)
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="最小完整 agent harness(OpenAI 兼容)")
    p.add_argument("task", nargs="*", help="一次性任务;留空进入 REPL")
    p.add_argument("--version", action="version", version=f"mini-harness {__version__}")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容端点根地址(默认 %(default)s)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--workspace", default=".", help="工具可访问的根目录,默认当前目录")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="最多几轮工具调用")
    p.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS, help="上下文裁剪预算(token)")
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help="采样温度,默认 0(工具调用更稳定)")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="单轮输出上限,默认 8192")
    p.add_argument("--ask", action="store_true", help="每次执行 shell 前人工确认")
    p.add_argument("--no-shell", action="store_true", help="禁用 run_bash")
    p.add_argument("--allow-dangerous", action="store_true", help="允许危险命令(慎用)")
    p.add_argument("--trace-dir", type=Path, default=DEFAULT_TRACE_DIR, help="JSONL 轨迹目录")
    return p.parse_args(argv)


def build_agent(args: argparse.Namespace) -> tuple[Agent, Path | None]:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        sys.exit(f"工作区不存在: {workspace}")

    args.trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = args.trace_dir / time.strftime("run-%Y%m%d-%H%M%S.jsonl")

    client = ChatClient(
        args.base_url, args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    tools = ToolRegistry(
        workspace,
        allow_shell=not args.no_shell,
        ask=args.ask,
        allow_dangerous=args.allow_dangerous,
    )
    agent = Agent(
        client, tools,
        max_turns=args.max_turns,
        context_tokens=args.context_tokens,
        on_event=Tracer(trace_path),
    )
    return agent, trace_path


def repl(agent: Agent) -> None:
    print("交互模式:输入任务,/exit 退出。")
    while True:
        try:
            task = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if task in ("/exit", "exit", "quit"):
            return
        if task:
            print(agent.run(task))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    agent, trace_path = build_agent(args)

    if "deepseek.com" in args.base_url and not os.environ.get(API_KEY_ENV):
        print(f"[提示] 未设置环境变量 {API_KEY_ENV}，请求可能返回 401。", file=sys.stderr)

    print(f"model={args.model}  workspace={agent.tools.workspace}")
    if trace_path:
        print(f"trace={trace_path}")

    if args.task:
        print(agent.run(" ".join(args.task)))
        return 0

    repl(agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
