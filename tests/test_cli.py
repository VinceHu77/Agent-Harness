"""CLI 层：参数默认值，以及 REPL 的调用约定（防止 main 和 repl 签名再次对不上）。"""

from __future__ import annotations

import unittest
from unittest import mock

import harness


class FakeAgent:
    def __init__(self) -> None:
        self.seen: list = []

    def run(self, task: str, on_text=None) -> str:
        self.seen.append((task, on_text))
        return "ok"


class TestParseArgs(unittest.TestCase):
    def test_defaults(self) -> None:
        args = harness.parse_args([])
        self.assertEqual(args.max_turns, 20)
        self.assertEqual(args.context_tokens, 64_000)
        self.assertEqual(args.temperature, 0.0)
        self.assertEqual(args.max_tokens, 8192)
        self.assertFalse(args.no_stream)

    def test_no_stream_flag(self) -> None:
        self.assertTrue(harness.parse_args(["--no-stream"]).no_stream)


class TestRepl(unittest.TestCase):
    def test_accepts_on_text_and_forwards_it(self) -> None:
        agent = FakeAgent()
        callback = lambda piece: None  # noqa: E731
        with mock.patch("builtins.input", side_effect=["你好", "/exit"]), \
                mock.patch("builtins.print"):
            harness.repl(agent, on_text=callback)
        self.assertEqual(agent.seen, [("你好", callback)])


if __name__ == "__main__":
    unittest.main()
