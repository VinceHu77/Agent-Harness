"""客服世界：订单、客户、优惠券、工单的 SQLite 存储。

这一步只提供「读」的能力（建表 + 种子数据 + query），
写操作和事务预览留给后面的步骤。

设计上的两个考虑：
- 用 SQLite 而不是 JSON：后面做 dry-run（BEGIN → ROLLBACK）和 fork（复制文件）
  时不用换存储；
- 所有函数都接受 ``path``：测试用临时文件，互不污染。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["DEFAULT_DB", "connect", "init_schema", "reset", "seed", "query"]

DEFAULT_DB = Path("world.db")

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
"""

# 种子数据：3 个客户、5 个订单、2 张券
CUSTOMERS = [
    ("CUST-01", "张伟", "gold",   1200, "active", "T1"),
    ("CUST-02", "李娜", "silver",  300, "active", "T1"),
    ("CUST-03", "王强", "basic",     0, "closed", "T1"),
]

ORDERS = [
    ("ORD-1001", "CUST-01", "paid",      30000,    0, '[{"name":"机械键盘","qty":1}]', "T1"),
    ("ORD-1002", "CUST-01", "shipped",   12900,    0, '[{"name":"鼠标","qty":1}]',     "T1"),
    ("ORD-1003", "CUST-02", "delivered",  5900,    0, '[{"name":"鼠标垫","qty":1}]',   "T1"),
    ("ORD-1004", "CUST-02", "delivered", 19900, 5000, '[{"name":"耳机","qty":1}]',     "T1"),
    ("ORD-1005", "CUST-03", "closed",     8800,    0, '[{"name":"数据线","qty":2}]',   "T1"),
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
        for table in ("tickets", "coupons", "orders", "customers"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    init_schema(path)
    seed(path)


def query(sql: str, params: tuple = (), *, path: Path = DEFAULT_DB) -> list[dict]:
    """只读查询，返回 dict 列表。"""
    conn = connect(path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
