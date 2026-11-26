"""客服世界：客户、订单、优惠券、工单、审计账本（SQLite）。

设计上的两个考虑：
- 用 SQLite 而不是 JSON：dry-run（BEGIN → ROLLBACK）和 fork（复制文件）
  不用换存储；
- 所有函数都接受 ``path``：测试用临时文件，互不污染。

``CURRENT_TENANT`` 是当前客服所属租户；跨租户的订单既读不到也改不了。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

__all__ = ["CURRENT_TENANT", "DEFAULT_DB", "connect", "execute", "init_schema", "insert",
           "log_action", "reset", "seed", "query"]

DEFAULT_DB = Path("world.db")

# 当前客服只服务 T1 租户
CURRENT_TENANT = "T1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    tier       TEXT NOT NULL,          -- basic / silver / gold
    points     INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,          -- active / closed
    tenant_id  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id             TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL,
    status         TEXT NOT NULL,      -- paid / shipped / delivered / closed
    paid_cents     INTEGER NOT NULL,
    refunded_cents INTEGER NOT NULL DEFAULT 0,
    items_json     TEXT NOT NULL DEFAULT '[]',
    tenant_id      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
    code        TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status      TEXT NOT NULL,         -- issued / used
    value_cents INTEGER NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    rule        TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json  TEXT NOT NULL DEFAULT '{}'
);
"""

# 种子数据：4 个客户、7 个订单、2 张券（含跨租户与边界数据）
CUSTOMERS = [
    ("CUST-01", "张伟", "gold",   1200, "active", "T1"),
    ("CUST-02", "李娜", "silver",  300, "active", "T1"),
    ("CUST-03", "王强", "basic",     0, "closed", "T1"),
    ("CUST-04", "赵敏", "basic",     0, "active", "T2"),   # 另一个租户
]

ORDERS = [
    ("ORD-1001", "CUST-01", "paid",      30000,    0, '[{"name":"机械键盘","qty":1}]', "T1"),
    ("ORD-1002", "CUST-01", "shipped",   12900,    0, '[{"name":"鼠标","qty":1}]',     "T1"),
    ("ORD-1003", "CUST-02", "delivered",  5900,    0, '[{"name":"鼠标垫","qty":1}]',   "T1"),
    ("ORD-1004", "CUST-02", "delivered", 19900, 5000, '[{"name":"耳机","qty":1}]',     "T1"),
    ("ORD-1005", "CUST-03", "closed",     8800,    0, '[{"name":"数据线","qty":2}]',   "T1"),
    ("ORD-1006", "CUST-01", "delivered",100000,    0, '[{"name":"显示器","qty":1}]',   "T1"),
    ("ORD-2001", "CUST-04", "delivered", 50000,    0, '[{"name":"摄像头","qty":1}]',   "T2"),
]

COUPONS = [
    ("CPN-10", "CUST-01", "issued", 2000, "2026-12-31"),
    ("CPN-20", "CUST-02", "used",   1000, "2026-06-30"),
]


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """打开数据库，行结果用 dict 风格访问（row["id"]）。"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path: Path = DEFAULT_DB) -> None:
    """建表（幂等）。"""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def seed(path: Path = DEFAULT_DB) -> None:
    """写入种子数据（幂等：已存在的行会跳过）。"""
    conn = connect(path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO customers VALUES (?,?,?,?,?,?)", CUSTOMERS)
        conn.executemany(
            "INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?)", ORDERS)
        conn.executemany(
            "INSERT OR IGNORE INTO coupons VALUES (?,?,?,?,?)", COUPONS)
        conn.commit()
    finally:
        conn.close()


def reset(path: Path = DEFAULT_DB) -> None:
    """清空并重建 —— 命令行 ``--reset-world`` 用这个。"""
    conn = connect(path)
    try:
        for table in ("ledger", "tickets", "coupons", "orders", "customers"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    init_schema(path)
    seed(path)


def log_action(tool: str, args: dict, decision: str, *,
               rule: str = "", before: dict | None = None,
               after: dict | None = None, path: Path = DEFAULT_DB) -> int:
    """往 ledger 追加一条审计记录（不可逆动作的 before/after）。"""
    return insert(
        "INSERT INTO ledger (tool, args_json, decision, rule, before_json, after_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            tool,
            json.dumps(args, ensure_ascii=False),
            decision,
            rule,
            json.dumps(before or {}, ensure_ascii=False),
            json.dumps(after or {}, ensure_ascii=False),
        ),
        path=path,
    )


def query(sql: str, params: tuple = (), *, path: Path = DEFAULT_DB) -> list[dict]:
    """只读查询，返回 dict 列表。"""
    conn = connect(path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def insert(sql: str, params: tuple = (), *, path: Path = DEFAULT_DB) -> int:
    """执行 INSERT，返回新行的 id。"""
    conn = connect(path)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def execute(sql: str, params: tuple = (), *, path: Path = DEFAULT_DB) -> int:
    """执行 UPDATE/DELETE，返回受影响的行数。"""
    conn = connect(path)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
