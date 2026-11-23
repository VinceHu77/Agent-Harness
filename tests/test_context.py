"""上下文裁剪：丢轮次、不破坏 tool 配对、保留任务目标。"""

from __future__ import annotations

import unittest

from loop import count_tokens, fit_context


def _round(i: int, pad: int = 400) -> list[dict]:
    """造一轮 assistant(tool_calls) + tool 结果。"""
    return [
        {"role": "assistant", "content": f"第{i}轮回答", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "list_dir", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * pad},
    ]


def _long_conversation() -> list[dict]:
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "u" * 400}]
    for i in range(8):
        msgs += _round(i)
    return msgs


class TestFitContext(unittest.TestCase):
    def test_short_untouched(self) -> None:
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        self.assertIs(fit_context(msgs, 1000), msgs)

    def test_drops_oldest_rounds(self) -> None:
        msgs = _long_conversation()
        out = fit_context(msgs, 400)
        self.assertLess(len(out), len(msgs))
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(out[1]["role"], "user")

    def test_no_orphan_tool_message(self) -> None:
        out = fit_context(_long_conversation(), 400)
        body = out[2:]
        if body:
            self.assertNotEqual(body[0]["role"], "tool")

    def test_fits_budget_or_minimum(self) -> None:
        out = fit_context(_long_conversation(), 400)
        self.assertTrue(count_tokens(out) <= 400 or len(out) <= 3)


if __name__ == "__main__":
    unittest.main()
