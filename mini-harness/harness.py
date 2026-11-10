#!/usr/bin/env python3
"""Mini Harness —— 最小但完整的 agent harness。

    python3 harness.py "任务描述"        # 一次性执行
    python3 harness.py                    # 交互式 REPL

只依赖 Python 标准库。默认连接本机 llama-server:
    http://127.0.0.1:8080/v1 , model = qwen-local
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from llm import ChatClient
from loop import Agent
from tools import ToolRegistry


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Printer:
    """把 Agent 事件同时打印到终端,并写入 JSONL trace(可观测性)。"""

    def __init__(self, trace_path: Path | None):
        self.trace_path = trace_path

    def __call__(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "request":
            print(f"\n── 第 {event['turn']} 轮 ──")
        elif kind == "response":
            for call in event["tool_calls"]:
                print(f"  → {call['name']}({clip(call['arguments'], 160)})")
        elif kind == "tool":
            print(f"  ← {clip(event['result'], 400)}")

        if self.trace_path:
            try:
                with self.trace_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"t": time.strftime("%H:%M:%S"), **event}, ensure_ascii=False) + "\n")
            except OSError:
                pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="最小完整 agent harness(OpenAI 兼容)")
    p.add_argument("task", nargs="*", help="一次性任务;留空进入 REPL")
    p.add_argument("--base-url", default=os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1"))
    p.add_argument("--model", default=os.environ.get("LLAMA_MODEL", "qwen-local"))
    p.add_argument("--workspace", default=".", help="工具可访问的根目录,默认当前目录")
    p.add_argument("--max-turns", type=int, default=20, help="最多几轮工具调用")
    p.add_argument("--context-chars", type=int, default=20000, help="上下文裁剪预算(字符)")
    p.add_argument("--ask", action="store_true", help="每次执行 shell 前人工确认")
    p.add_argument("--no-shell", action="store_true", help="禁用 run_bash")
    p.add_argument("--allow-dangerous", action="store_true", help="允许危险命令(慎用)")
    p.add_argument("--trace-dir", type=Path, default=Path("runs"), help="JSONL 轨迹目录")
    return p.parse_args()


def build_agent(args: argparse.Namespace) -> tuple[Agent, Path | None]:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        sys.exit(f"工作区不存在: {workspace}")

    trace_path: Path | None = None
    try:
        args.trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = args.trace_dir / time.strftime("run-%Y%m%d-%H%M%S.jsonl")
    except OSError:
        trace_path = None

    client = ChatClient(args.base_url, args.model)
    tools = ToolRegistry(
        workspace,
        allow_shell=not args.no_shell,
        ask=args.ask,
        allow_dangerous=args.allow_dangerous,
    )
    agent = Agent(
        client, tools,
        max_turns=args.max_turns,
        context_chars=args.context_chars,
        on_event=Printer(trace_path),
    )
    return agent, trace_path


def main() -> None:
    args = parse_args()
    agent, trace_path = build_agent(args)

    print(f"model={args.model}  workspace={agent.tools.workspace}")
    if trace_path:
        print(f"trace={trace_path}")

    if args.task:
        print(agent.run(" ".join(args.task)))
        return

    print("交互模式:输入任务,/exit 退出。")
    while True:
        try:
            task = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if task in ("/exit", "exit", "quit"):
            break
        if task:
            print(agent.run(task))


if __name__ == "__main__":
    main()
