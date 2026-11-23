"""mini-harness 的测试（只依赖标准库 unittest，不需要联网）。

测试会临时关掉 tiktoken：加载词表要几秒，而这里不关心精确 token 数。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# 让 tests 能 import 到上一层的 llm / loop / tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 关掉 tiktoken，省掉加载词表的时间
os.environ.setdefault("MINI_HARNESS_TOKENIZER", "off")
