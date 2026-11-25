"""循环：用假客户端覆盖「直接回答 / 调工具 / 截断 / 模型失败 / 轮数上限」。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from llm import Completion, LLMError, ToolCall
from loop import Agent
from tools import ToolRegistry


def _noop(_piece: str) -> None:
    """流式回调的占位：测试不关心输出。"""


class FakeClient:
    """按预设顺序返回响应，并记录每次收到的 messages。"""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kwargs) -> Completion:
        self.calls.append([dict(m) for m in messages])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestAgent(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tools = ToolRegistry(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_direct_answer(self) -> None:
        events: list[dict] = []
        agent = Agent(FakeClient([Completion(content="done")]), self.tools,
                      on_text=_noop, on_event=events.append)
        self.assertEqual(agent.run("hi"), "done")
        self.assertEqual([e["kind"] for e in events], ["request", "response"])

    def test_tool_call_roundtrip(self) -> None:
        client = FakeClient([
            Completion(content="", tool_calls=[ToolCall("c1", "list_dir", "{}")]),
            Completion(content="ok"),
        ])
        agent = Agent(client, self.tools, on_text=_noop)
        self.assertEqual(agent.run("看看目录"), "ok")
        self.assertEqual(len(client.calls), 2)
        second = client.calls[1]
        self.assertTrue(any(m["role"] == "tool" and m["tool_call_id"] == "c1"
                            for m in second))

    def test_finish_reason_length(self) -> None:
        agent = Agent(FakeClient([Completion(content="半截", finish_reason="length")]),
                      self.tools, on_text=_noop)
        self.assertIn("max_tokens", agent.run("写长文"))

    def test_llm_error_becomes_text(self) -> None:
        agent = Agent(FakeClient([LLMError("boom")]), self.tools, on_text=_noop)
        self.assertIn("模型调用失败", agent.run("hi"))

    def test_max_turns(self) -> None:
        responses = [Completion(content="", tool_calls=[ToolCall(f"c{i}", "list_dir", "{}")])
                     for i in range(3)]
        agent = Agent(FakeClient(responses), self.tools, on_text=_noop, max_turns=3)
        self.assertIn("最大轮数", agent.run("死循环"))


if __name__ == "__main__":
    unittest.main()
