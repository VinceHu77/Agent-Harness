"""让 `python -m harness` 等价于 `python -m harness.cli`。

注意：本包内的模块大量使用相对导入（`from ..core.action import ...`），
因此**只能作为包运行**，不能 `python harness/bench/runner.py` 这样直接跑脚本。
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
