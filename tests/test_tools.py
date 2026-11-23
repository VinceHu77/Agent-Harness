"""工具注册表：正常调用 + 所有错误路径都变成字符串。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import ToolRegistry


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name).resolve()
        (self.ws / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
        self.reg = ToolRegistry(self.ws)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schemas_shape(self) -> None:
        schemas = self.reg.schemas()
        self.assertEqual(len(schemas), 4)
        self.assertEqual(schemas[0]["type"], "function")
        self.assertIn("list_dir", self.reg.names())

    def test_list_dir(self) -> None:
        self.assertIn("hello.txt", self.reg.call("list_dir", "{}"))

    def test_read_write_roundtrip(self) -> None:
        self.assertIn("已写入", self.reg.call(
            "write_file", '{"path": "sub/a.txt", "content": "hi\\n"}'))
        self.assertIn("hi", self.reg.call("read_file", '{"path": "sub/a.txt"}'))

    def test_unknown_tool(self) -> None:
        out = self.reg.call("nope", "{}")
        self.assertTrue(out.startswith("[error]"))
        self.assertIn("未知工具", out)

    def test_bad_json(self) -> None:
        self.assertIn("参数不是合法 JSON", self.reg.call("list_dir", "{not json"))

    def test_path_escape_becomes_error(self) -> None:
        out = self.reg.call("read_file", '{"path": "../secret.txt"}')
        self.assertTrue(out.startswith("[error]"))
        self.assertIn("路径越界", out)

    def test_bad_argument_name(self) -> None:
        self.assertIn("参数不匹配",
                      self.reg.call("read_file", '{"path": "hello.txt", "bogus": 1}'))

    def test_shell_disabled(self) -> None:
        reg = ToolRegistry(self.ws, allow_shell=False)
        self.assertIn("已禁用", reg.call("run_bash", '{"command": "echo hi"}'))

    def test_dangerous_command_blocked(self) -> None:
        self.assertIn("命令被拦截", self.reg.call("run_bash", '{"command": "rm -rf /"}'))

    def test_command_runs(self) -> None:
        out = self.reg.call("run_bash", '{"command": "echo safe"}')
        self.assertIn("[exit 0]", out)
        self.assertIn("safe", out)


if __name__ == "__main__":
    unittest.main()
