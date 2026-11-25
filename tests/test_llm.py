"""模型层：请求体构造（thinking / 采样参数）与响应归一化。"""

from __future__ import annotations

import json
import unittest

from llm import ChatClient


class CaptureClient(ChatClient):
    """不真的发请求，只把 payload 截下来。"""

    def __init__(self, base_url: str, **kwargs) -> None:
        super().__init__(base_url, "deepseek-flash", **kwargs)
        self.payload: dict | None = None

    def _post_with_retry(self, payload: dict) -> dict:
        self.payload = payload
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


class TestPayload(unittest.TestCase):
    def test_deepseek_disables_thinking(self) -> None:
        client = CaptureClient("https://api.deepseek.com")
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(client.payload["thinking"], {"type": "disabled"})

    def test_default_temperature_is_zero(self) -> None:
        client = CaptureClient("https://api.deepseek.com")
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(client.payload["temperature"], 0.0)

    def test_local_endpoint_gets_no_thinking(self) -> None:
        client = CaptureClient("http://127.0.0.1:8080/v1")
        client.chat([{"role": "user", "content": "hi"}])
        self.assertNotIn("thinking", client.payload)

    def test_max_tokens_can_be_overridden(self) -> None:
        client = CaptureClient("https://api.deepseek.com", max_tokens=1024)
        client.chat([{"role": "user", "content": "hi"}], max_tokens=64)
        self.assertEqual(client.payload["max_tokens"], 64)

    def test_tools_field_shape(self) -> None:
        client = CaptureClient("https://api.deepseek.com")
        client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
        self.assertEqual(client.payload["tool_choice"], "auto")
        self.assertIn("tools", client.payload)


class TestParse(unittest.TestCase):
    def test_tool_calls_normalized(self) -> None:
        comp = ChatClient._parse({
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"total_tokens": 12},
        })
        self.assertEqual(comp.tool_calls[0].name, "read_file")
        self.assertEqual(comp.tool_calls[0].arguments, '{"path": "a"}')
        self.assertEqual(comp.finish_reason, "tool_calls")
        self.assertEqual(comp.usage["total_tokens"], 12)


if __name__ == "__main__":
    unittest.main()


class TestStream(unittest.TestCase):
    @staticmethod
    def _sse(obj: dict) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False)

    def test_content_and_tool_calls_are_assembled(self) -> None:
        lines = [
            self._sse({"choices": [{"delta": {"content": "你"}}]}),
            self._sse({"choices": [{"delta": {"content": "好"}}]}),
            self._sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "c1",
                "function": {"name": "read_file", "arguments": '{"path":'}}]}}]}),
            self._sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": ' "a"}'}}]}}]}),
            self._sse({"choices": [{"finish_reason": "tool_calls"}]}),
            self._sse({"usage": {"total_tokens": 7}}),
            "data: [DONE]",
        ]
        got: list[str] = []
        comp = ChatClient._consume_stream(lines, got.append)

        self.assertEqual(comp.content, "你好")
        self.assertEqual(got, ["你", "好"])
        self.assertEqual(comp.tool_calls[0].name, "read_file")
        self.assertEqual(comp.tool_calls[0].arguments, '{"path": "a"}')
        self.assertEqual(comp.finish_reason, "tool_calls")
        self.assertEqual(comp.usage["total_tokens"], 7)

    def test_ignores_keepalive_and_bad_json(self) -> None:
        lines = ["", ": keep-alive", "data: {not json", "data: [DONE]"]
        comp = ChatClient._consume_stream(lines)
        self.assertEqual(comp.content, "")
        self.assertEqual(comp.tool_calls, [])
