"""安全策略：路径围栏、危险命令、输出截断。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import clip, dangerous_reason
from tools import ToolRegistry


class TestPathFence(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name).resolve()
        self.reg = ToolRegistry(self.ws)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_relative_inside(self) -> None:
        self.assertEqual(self.reg.resolve("a/b.txt"), self.ws / "a" / "b.txt")

    def test_absolute_inside(self) -> None:
        target = self.ws / "x.txt"
        self.assertEqual(self.reg.resolve(str(target)), target)

    def test_relative_escape(self) -> None:
        with self.assertRaises(PermissionError):
            self.reg.resolve("../outside.txt")

    def test_absolute_escape(self) -> None:
        with self.assertRaises(PermissionError):
            self.reg.resolve("/etc/passwd")

    def test_symlink_escape(self) -> None:
        link = self.ws / "link"
        try:
            link.symlink_to("/etc")
        except OSError:
            self.skipTest("当前系统不支持创建符号链接")
        with self.assertRaises(PermissionError):
            self.reg.resolve("link/passwd")


class TestDangerous(unittest.TestCase):
    def test_rm_root(self) -> None:
        self.assertIsNotNone(dangerous_reason("rm -rf /"))

    def test_rm_home(self) -> None:
        self.assertIsNotNone(dangerous_reason("rm -rf ~"))

    def test_mkfs(self) -> None:
        self.assertIsNotNone(dangerous_reason("mkfs.ext4 /dev/sda1"))

    def test_fork_bomb(self) -> None:
        self.assertIsNotNone(dangerous_reason(":(){ :|:& };:"))

    def test_safe_commands(self) -> None:
        self.assertIsNone(dangerous_reason("ls -la"))
        self.assertIsNone(dangerous_reason("git status"))


class TestClip(unittest.TestCase):
    def test_short_untouched(self) -> None:
        self.assertEqual(clip("abc", 10), "abc")

    def test_long_truncated(self) -> None:
        out = clip("x" * 100, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("截断", out)
        self.assertIn("100", out)


if __name__ == "__main__":
    unittest.main()
