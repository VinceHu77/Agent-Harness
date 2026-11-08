"""小工具：确定性哈希（用于可复现的噪声）与序列化尺寸。"""

from __future__ import annotations

import hashlib
import json


def stable_unit(text: str) -> float:
    """把任意字符串映射到 [0,1) 的确定性浮点数。

    评测里凡是需要「随机性」（PreVal 噪声、dry-run 保真度翻转）都走这里，
    好处是：同一 request_id 在任何策略下拿到同一个噪声，
    因此 B1 与 Ours 的差异只来自架构，不来自抽样运气。
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def json_bytes(obj: object) -> int:
    return len(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
