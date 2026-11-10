"""工具层:工具注册表 + 内置工具,全部限制在 workspace 内。

两个职责:
1. 给模型看:schemas() 生成 JSON Schema,随每次请求发给模型。
2. 替模型做:call() 执行工具,把结果(包括错误)变成字符串回填给模型。

安全边界都在这层:路径围栏、超时、输出截断、危险命令拦截、可选人工确认。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_OUTPUT = 8000  # 单个工具结果回填给模型的最大字符数,防止一条输出撑爆上下文

# 极简"安全带",不是沙箱:能拦明显手滑,拦不住有意绕过。
_DANGEROUS = [
    (re.compile(r"\brm\s+[^|;&\n]*\s(/|~|\$HOME)(\s|$)"), "删除根目录或家目录"),
    (re.compile(r"\bmkfs\b"), "格式化文件系统"),
    (re.compile(r"\bdd\b[^|;&\n]*\bof=/dev/"), "直接写块设备"),
    (re.compile(r"\b(shutdown|reboot|poweroff)\b"), "关机/重启"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
]


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[截断,原长 {len(text)} 字符]"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]


class ToolRegistry:
    def __init__(self, workspace: Path, *, allow_shell: bool = True, ask: bool = False,
                 allow_dangerous: bool = False):
        self.workspace = workspace.resolve()
        self.allow_shell = allow_shell
        self.ask = ask
        self.allow_dangerous = allow_dangerous
        self._tools: dict[str, Tool] = {}
        self._register_builtins()

    # ---- 给模型看的接口 ----

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    # ---- 替模型做的接口 ----

    def call(self, name: str, raw_args) -> str:
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
        except TypeError as exc:  # 参数名/数量不对
            return f"[error] 参数不匹配: {exc}"
        except PermissionError as exc:
            return f"[error] {exc}"
        except Exception as exc:  # 工具内部错误也要回填,模型才可能自纠
            return f"[error] {type(exc).__name__}: {exc}"

        return _clip(str(result), MAX_OUTPUT)

    # ---- 内置工具 ----

    def _register_builtins(self) -> None:
        self.add(Tool(
            "list_dir",
            "List files and subdirectories of a directory. Paths are relative to the workspace.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path, default '.'"}},
            },
            self._list_dir,
        ))
        self.add(Tool(
            "read_file",
            "Read a UTF-8 text file with line numbers. Use offset/limit for large files.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                    "offset": {"type": "integer", "description": "1-based first line, default 1"},
                    "limit": {"type": "integer", "description": "Max lines to return, default 400"},
                },
                "required": ["path"],
            },
            self._read_file,
        ))
        self.add(Tool(
            "write_file",
            "Create or overwrite a UTF-8 text file. Parent directories are created automatically. Always read a file before overwriting it.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
            self._write_file,
        ))
        self.add(Tool(
            "run_bash",
            "Run a shell command in the workspace root. Returns exit code, stdout and stderr. Use it to run tests, scripts, git, etc.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Seconds, default 30, max 120"},
                },
                "required": ["command"],
            },
            self._run_bash,
        ))

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        p = (self.workspace / p).resolve() if not p.is_absolute() else p.resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise PermissionError(f"路径越界: {path}(只允许工作区 {self.workspace} 内)")
        return p

    def _list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"[error] 目录不存在: {path}"
        if not p.is_dir():
            return f"[error] {path} 不是目录"
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for entry in entries[:200]:
            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            else:
                try:
                    lines.append(f"  {entry.name}  ({entry.stat().st_size} B)")
                except OSError:
                    lines.append(f"  {entry.name}")
        more = f"\n  ... 还有 {len(entries) - 200} 项" if len(entries) > 200 else ""
        return f"[{path} 共 {len(entries)} 项]\n" + ("\n".join(lines) or "  (空)") + more

    def _read_file(self, path: str, offset: int = 1, limit: int = 400) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"[error] 文件不存在: {path}"
        if p.is_dir():
            return f"[error] {path} 是目录,请用 list_dir"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(offset) - 1)
        end = min(len(lines), start + max(1, int(limit)))
        body = "\n".join(f"{i + 1:>5}  {line}" for i, line in enumerate(lines[start:end], start))
        header = f"[{path} 第 {start + 1}-{end} 行,共 {len(lines)} 行]"
        if end < len(lines):
            header += f"(未读完,可用 offset={end + 1} 继续)"
        return header + "\n" + body

    def _write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        if p.is_dir():
            return f"[error] {path} 是目录"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}({len(content)} 字符,{content.count(chr(10)) + 1} 行)"

    def _run_bash(self, command: str, timeout: int = 30) -> str:
        if not self.allow_shell:
            return "[error] shell 工具已禁用(--no-shell)"

        for pattern, reason in _DANGEROUS:
            if pattern.search(command) and not self.allow_dangerous:
                return f"[error] 命令被拦截({reason})。如确需执行,请用 --allow-dangerous 启动。"

        if self.ask and not self._confirm(command):
            return "[error] 用户拒绝了这次命令执行。"

        timeout = max(1, min(int(timeout), 120))
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.workspace,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") + (exc.stderr or "")
            return f"[error] 命令超时({timeout}s)\n{_clip(partial, MAX_OUTPUT)}"

        parts = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            parts.append("[stderr]\n" + proc.stderr)
        body = "\n".join(parts).strip() or "(无输出)"
        return f"[exit {proc.returncode}]\n{body}"

    def _confirm(self, command: str) -> bool:
        try:
            answer = input(f"\n[权限] 要执行以下命令吗?\n  {command}\n[y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")
