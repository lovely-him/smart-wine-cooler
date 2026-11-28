"""SQLite 连接与建库。

数据库路径不存进数据库——那个信息走「命令行 > 环境变量 > 约定位置」，
库文件坏了程序仍能启动、能报错、能重建。

并发约定：单进程多线程，WAL + 每个线程独立连接 + 只允许一个写者。

**一条连接不能给两个线程用**：两个线程同时在一条连接上取结果，行数据与列名会串位，
报出来的是 `IndexError: tuple index out of range`、`JSONDecodeError` 这类看起来
跟数据库毫无关系的错。所以 `conn` 必须每线程一条。
"""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 4
SCHEMA_FILE = Path(__file__).with_name("schema.sql")

MIGRATIONS = {
    4: ["ALTER TABLE layer_record ADD COLUMN layer_name TEXT NOT NULL DEFAULT ''"],
}


def now():
    """写库用的时间戳：带时区的 ISO 字符串，秒级。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _migrate(conn, stored):
    for version in range(stored + 1, SCHEMA_VERSION + 1):
        for sql in MIGRATIONS.get(version, []):
            conn.execute(sql)


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class Database:
    """每线程一条连接，加一把全局写锁。读可以并发，写必须串行——SQLite 只允许一个写者。"""

    def __init__(self, path):
        self.path = Path(path)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._opened = []
        self._opened_lock = threading.Lock()

    def _thread_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.path)
            self._local.conn = conn
            with self._opened_lock:
                self._opened.append(conn)
        return conn

    @property
    def conn(self):
        return self._thread_conn()

    def init_schema(self):
        with self._write_lock:
            stored = self._stored_version()
            if stored > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库结构版本 {stored} 比程序要求的 {SCHEMA_VERSION} 新，"
                    f"用新程序打开，或删除 {self.path} 后重启"
                )
            conn = self._thread_conn()
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            if stored:
                _migrate(conn, stored)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        return self.schema_version()

    def _stored_version(self):
        try:
            row = self._thread_conn().execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["value"]) if row else 0

    def schema_version(self):
        row = self._thread_conn().execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def write(self, fn):
        """在写锁里执行 fn(conn) 并提交。"""
        with self._write_lock:
            conn = self._thread_conn()
            try:
                result = fn(conn)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def drop_thread_conn(self):
        """线程退出前关掉它自己的连接。一次性线程（每次观察起的那个）必须调，否则句柄一直涨。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._opened_lock:
            if conn in self._opened:
                self._opened.remove(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close(self):
        with self._opened_lock:
            opened, self._opened = self._opened, []
        for conn in opened:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.conn = None
