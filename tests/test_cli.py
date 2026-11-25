"""CLI 层：参数默认值，以及 REPL 的调用约定（防止 main 和 repl 签名再次对不上）。"""

from __future__ import annotations

import unittest
from unittest import mock

import harness


class FakeAgent:
    def __init__(self) -> None:
        self.seen: list = []

    def run(self, task: str, on_text) -> str:
        self.seen.append((task, on_text))
        return "ok"


class TestParseArgs(unittest.TestCase):
    def test_defaults(self) -> None:
        args = harness.parse_args([])
        self.assertEqual(args.max_turns, 20)
        self.assertEqual(args.context_tokens, 64_000)
        self.assertEqual(args.temperature, 0.0)
        self.assertEqual(args.max_tokens, 8192)


class TestRepl(unittest.TestCase):
    def test_repl_streams_with_internal_callback(self) -> None:
        agent = FakeAgent()
        with mock.patch("builtins.input", side_effect=["你好", "/exit"]), \
                mock.patch("builtins.print"):
            harness.repl(agent)

        self.assertEqual(len(agent.seen), 1)
        task, on_text = agent.seen[0]
        self.assertEqual(task, "你好")
        self.assertTrue(callable(on_text))


if __name__ == "__main__":
    unittest.main()
