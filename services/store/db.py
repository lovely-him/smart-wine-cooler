"""SQLite 建库与连接。只用标准库，板上无需额外依赖。

表结构与 docs/inventory-loop.md §2 对齐：
- observation 只追加，存"看到的计数"，不存差值
- baseline 版本化，只能由"确认"整体替换
- confirmation 保存**系统报的**与**用户提交的**两份全文（Q17：负样本与漏检信号的唯一来源）
- event 只在净变化时记一条，只读可加备注
"""

import os
import sqlite3

# ── 路径与常量 ──
SCHEMA = """
CREATE TABLE IF NOT EXISTS category (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baseline (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    payload   TEXT NOT NULL,
    order_payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scan (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    state     TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observation (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id   INTEGER NOT NULL DEFAULT 0,
    camera    TEXT NOT NULL,
    epoch     INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    tier      TEXT NOT NULL,
    reasons   TEXT NOT NULL,
    count     INTEGER NOT NULL,
    detections TEXT NOT NULL,
    metrics   TEXT NOT NULL,
    frames    TEXT NOT NULL,
    baseline_id INTEGER,
    image     TEXT NOT NULL DEFAULT '',
    order_payload TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS confirmation (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    baseline_id INTEGER NOT NULL,
    reported  TEXT NOT NULL,
    submitted TEXT NOT NULL,
    corrections TEXT NOT NULL,
    baseline_delta TEXT NOT NULL,
    observation_ids TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reference (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    path      TEXT NOT NULL,
    feature   BLOB NOT NULL,
    dim       INTEGER NOT NULL,
    source    TEXT NOT NULL,
    quality   REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merge_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    from_category INTEGER NOT NULL,
    to_category INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    scan_id   INTEGER NOT NULL DEFAULT 0,
    camera    TEXT NOT NULL DEFAULT '',
    observation_id INTEGER NOT NULL DEFAULT 0,
    baseline_id INTEGER,
    note      TEXT NOT NULL DEFAULT ''
);
"""


# ── 函数 ──
def _migrate(conn):
    """旧库对齐：补新增的列，删已废弃的列。"""
    additions = [
        ("observation", "scan_id", "INTEGER NOT NULL DEFAULT 0"),
        ("event", "scan_id", "INTEGER NOT NULL DEFAULT 0"),
        ("baseline", "order_payload", "TEXT NOT NULL DEFAULT '{}'"),
        ("observation", "order_payload", "TEXT NOT NULL DEFAULT '[]'"),
    ]
    for table, column, decl in additions:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    obsolete = [("category", "archived"), ("category", "is_placeholder")]
    for table, column in obsolete:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()


def connect(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn
