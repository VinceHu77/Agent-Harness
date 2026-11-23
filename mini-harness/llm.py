"""模型层：一个最小的 OpenAI 兼容 chat client（只用标准库）。

llama-server 暴露的正是 OpenAI 兼容接口：

    POST {base_url}/chat/completions

所以这一层不绑定 llama.cpp。把 base_url 换成 vLLM / Ollama / 云端 API，
上层 harness 一行都不用改 —— 这就是「适配层」的意义。

API Key 只从环境变量 ``DEEPSEEK_API_KEY`` 读取，不写进代码、不入库。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

__all__ = ["ChatClient", "Completion", "LLMError", "ToolCall"]

API_KEY_ENV = "DEEPSEEK_API_KEY"

DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRIES = 2
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 8192


class LLMError(RuntimeError):
    """模型服务不可用，或返回了无法解析的响应。"""


@dataclass
class ToolCall:
    """一次工具调用请求。

    ``arguments`` 保持原始 JSON 字符串：解析失败要能回传给模型自纠，
    不应该在传输层就崩掉。
    """

    id: str
    name: str
    arguments: str


@dataclass
class Completion:
    """把各家响应差异收敛成这一个结构。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


def _api_key_for(base_url: str) -> str:
    """只有指向 deepseek.com 时返回 key。

    本地 llama-server / vLLM 不需要 key；把 key 发到第三方端点属于泄露。
    """
    if "deepseek.com" not in base_url:
        return ""
    return os.environ.get(API_KEY_ENV, "").strip()


class ChatClient:
    """同步、无流式的 chat client；重试只覆盖网络类临时错误。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        api_key: str | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.retries = retries
        # 显式传入时以传入值为准（测试用）；否则按端点决定
        self.api_key = _api_key_for(base_url) if api_key is None else api_key

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        """发一次请求，返回归一化后的 Completion。"""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        return self._parse(self._post_with_retry(payload))

    # ---- 传输 ----

    def _post_with_retry(self, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._post(payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))  # 线性退避
        raise LLMError(f"无法连接模型服务 {self.url}: {last}")

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(self.url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:800]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"响应不是合法 JSON: {exc}") from exc

    # ---- 归一化 ----

    @staticmethod
    def _parse(data: dict) -> Completion:
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
