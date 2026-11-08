"""副作用账本：单一事实源（append-only）。

相对旧版 runtime_ledger.py 的改动：
  * 存储从相对路径 JSON 改为 SQLite，避免 cwd 依赖与并发覆盖；
  * 补齐开题报告 4.1 要求的字段：request_id、幂等键、参数摘要、时间戳、
    分支来源（branch_id）、风险档、裁决来源、执行后世界哈希；
  * 追加写，不更新不删除 —— 账本本身就是「不可回滚状态」的证据。

world_hash 让「这条账本记录对应哪一刻的世界状态」可被独立复核：
它是 C1（副作用不可回滚）的可审计锚点。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    run_id       TEXT,
    scenario_id  TEXT,
    request_id   TEXT,
    session_id   TEXT,
    branch_id    TEXT,
    tool         TEXT,
    tier         INTEGER,
    irreversible INTEGER,
    arg_digest   TEXT,
    idem_key     TEXT,
    decision     TEXT,
    reason       TEXT,
    source       TEXT,
    risk_score   REAL,
    cost         REAL,
    isolation    TEXT,
    executed     INTEGER,
    outcome      TEXT,
    world_hash   TEXT,
    elapsed_ms   REAL
);
CREATE INDEX IF NOT EXISTS idx_ledger_idem ON ledger(idem_key);
CREATE INDEX IF NOT EXISTS idx_ledger_scenario ON ledger(scenario_id);
"""

_FIELDS = (
    "run_id",
    "scenario_id",
    "request_id",
    "session_id",
    "branch_id",
    "tool",
    "tier",
    "irreversible",
    "arg_digest",
    "idem_key",
    "decision",
    "reason",
    "source",
    "risk_score",
    "cost",
    "isolation",
    "executed",
    "outcome",
    "world_hash",
    "elapsed_ms",
)


class Ledger:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def append(self, **kw: Any) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        values = [ts] + [kw.get(f) for f in _FIELDS]
        placeholders = ",".join("?" * (len(_FIELDS) + 1))
        self.conn.execute(
            f"INSERT INTO ledger (ts,{','.join(_FIELDS)}) VALUES ({placeholders})", values
        )
        self.conn.commit()

    def already_executed(self, idem_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM ledger WHERE idem_key=? AND executed=1 LIMIT 1", (idem_key,)
        ).fetchone()
        return row is not None

    def rows(self) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM ledger ORDER BY seq")
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
