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
from typing import Callable

__all__ = ["ChatClient", "Completion", "LLMError", "ToolCall"]

API_KEY_ENV = "DEEPSEEK_API_KEY"

DEFAULT_TIMEOUT = 300.0
DEFAULT_RETRIES = 2
DEFAULT_TEMPERATURE = 0.0
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
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        # DeepSeek 独有参数（thinking / 专属 key）只在官方端点发送
        self.is_deepseek = "deepseek.com" in base_url
        # 显式传入时以传入值为准（测试用）；否则按端点决定
        self.api_key = _api_key_for(base_url) if api_key is None else api_key

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        """发一次请求，返回归一化后的 Completion。

        ``stream=True`` 时走 SSE：文本片段边到边回调 ``on_text``，
        工具调用分片会在内部拼好后再返回。
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # DeepSeek 默认开启 thinking（effort=high）。带 tools 时官方要求把每轮的
        # reasoning_content 原样回传，否则 400；本 harness 不保存它，所以显式关闭。
        if self.is_deepseek:
            payload["thinking"] = {"type": "disabled"}

        if stream:
            payload["stream"] = True
            # 让服务端在最后一块里带 usage，否则流式拿不到 token 数
            payload["stream_options"] = {"include_usage": True}
            return self._post_with_retry(payload, stream=True, on_text=on_text)

        return self._parse(self._post_with_retry(payload))

    # ---- 传输 ----

    def _post_with_retry(self, payload: dict, *, stream: bool = False,
                         on_text: Callable[[str], None] | None = None):
        """发请求。网络类错误重试；流式时若已经输出过内容则不再重试。"""
        last: Exception | None = None
        emitted = False

        def emit(piece: str) -> None:
            nonlocal emitted
            emitted = True
            if on_text:
                on_text(piece)

        for attempt in range(self.retries + 1):
            try:
                if stream:
                    return self._post_stream(payload, emit)
                return self._post(payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if emitted:
                    # 已经开始往终端吐字，再重试会重复输出
                    raise LLMError(f"流式中断: {exc}") from exc
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))  # 线性退避
        raise LLMError(f"无法连接模型服务 {self.url}: {last}")

    def _post_stream(self, payload: dict,
                     on_text: Callable[[str], None]) -> Completion:
        """POST 并解析 SSE；服务端忽略 stream 时自动退回普通 JSON。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=body, headers=headers)

        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:800]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc

        with resp:
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return self._parse(json.loads(resp.read()))
            return self._consume_stream(resp, on_text)

    @staticmethod
    def _consume_stream(lines,
                        on_text: Callable[[str], None] | None = None) -> Completion:
        """把 SSE 的 ``data:`` 行拼成 Completion。

        ``lines`` 可以是 bytes / str 的可迭代对象（单测直接喂列表即可）。
        工具调用是按 index 分片到达的，这里累计 id / name / arguments。
        """
        content_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish_reason = ""
        usage: dict = {}

        for raw in lines:
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    if on_text:
                        on_text(piece)
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index", 0))
                    slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        ordered = [
            ToolCall(id=calls[i]["id"] or f"call_{i}",
                     name=calls[i]["name"],
                     arguments=calls[i]["arguments"] or "{}")
            for i in sorted(calls)
        ]
        return Completion(
            content="".join(content_parts).strip(),
            tool_calls=ordered,
            finish_reason=finish_reason,
            usage=usage,
        )

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
