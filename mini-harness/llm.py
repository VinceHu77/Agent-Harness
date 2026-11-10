"""模型层：一个最小的 OpenAI 兼容 chat client（只用标准库）。

llama-server 暴露的正是 OpenAI 兼容接口:

    POST /v1/chat/completions

所以这一层不绑定 llama.cpp。把 base_url 换成 vLLM / Ollama / 云端 API，
上层 harness 一行都不用改 —— 这就是"适配层"的意义。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """模型服务不可用，或返回了无法解析的响应。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 原始 JSON 字符串。故意不在这里解析：解析失败要能回传给模型自纠。


@dataclass
class Completion:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


class ChatClient:
    def __init__(self, base_url: str, model: str, *, timeout: float = 300, retries: int = 2):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def chat(self, messages, tools=None, *, temperature: float = 0.3, max_tokens: int = 2048) -> Completion:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._post_with_retry(payload)
        return self._parse(data)

    # ---- 传输 ----

    def _post_with_retry(self, payload):
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._post(payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))  # 简单退避
        raise LLMError(f"无法连接模型服务 {self.url}: {last}")

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:800]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"响应不是合法 JSON: {exc}") from exc

    # ---- 归一化 ----

    def _parse(self, data) -> Completion:
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"响应格式异常: {str(data)[:500]}") from exc

        calls: list[ToolCall] = []
        for i, raw in enumerate(msg.get("tool_calls") or []):
            fn = raw.get("function") or {}
            args = fn.get("arguments")
            if not isinstance(args, str):  # 少数实现会直接给对象
                args = json.dumps(args or {}, ensure_ascii=False)
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{i}",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )

        return Completion(
            content=(msg.get("content") or "").strip(),
            tool_calls=calls,
            finish_reason=choice.get("finish_reason") or "",
            usage=data.get("usage") or {},
        )
