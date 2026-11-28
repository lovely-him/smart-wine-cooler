"""类别：名字、形状、详情，以及合并留下的别名。

名字在库里唯一，重名在写库时就会被数据库拦下——这里把它翻成 `NameTaken`。
"""

import json
import sqlite3

from . import now


class NameTaken(Exception):
    """类别名已存在。名字在库里是唯一的。"""


def create_category(db, name, shape="", details=None):
    def _write(conn):
        cur = conn.execute(
            "INSERT INTO category(name, shape, details, created_at) VALUES(?, ?, ?, ?)",
            (name, shape, json.dumps(details or {}, ensure_ascii=False), now()),
        )
        return cur.lastrowid

    try:
        return db.write(_write)
    except sqlite3.IntegrityError as exc:
        raise NameTaken(name) from exc


def list_categories(db):
    rows = db.conn.execute("SELECT * FROM category ORDER BY id").fetchall()
    refs = db.conn.execute("SELECT id, category_id, path FROM reference ORDER BY id").fetchall()
    by_category = {}
    for ref in refs:
        by_category.setdefault(ref["category_id"], []).append(
            {"id": ref["id"], "path": ref["path"]}
        )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "shape": row["shape"],
            "details": json.loads(row["details"]),
            "refs": by_category.get(row["id"], []),
        }
        for row in rows
    ]


def update_category(db, category_id, name, shape, details):
    def _write(conn):
        conn.execute(
            "UPDATE category SET name=?, shape=?, details=? WHERE id=?",
            (name, shape, json.dumps(details or {}, ensure_ascii=False), category_id),
        )

    try:
        return db.write(_write)
    except sqlite3.IntegrityError as exc:
        raise NameTaken(name) from exc


def category_usage(db, category_id):
    """历史里有多少件挂在这个类别上：观察里的实体 + 各次确认提交的那一份。"""
    live = db.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE category_id=?", (category_id,)
    ).fetchone()[0]
    stored = 0
    for row in db.conn.execute("SELECT submitted FROM checkpoint"):
        for layer in json.loads(row["submitted"]):
            stored += sum(
                1 for entity in layer.get("entities", []) if entity.get("categoryId") == category_id
            )
    return live + stored


def delete_category(db, category_id):
    """硬删，参考图一并删。返回要跟着删的参考图相对路径。"""

    def _write(conn):
        paths = [
            row["path"]
            for row in conn.execute("SELECT path FROM reference WHERE category_id=?", (category_id,))
        ]
        conn.execute("DELETE FROM reference WHERE category_id=?", (category_id,))
        conn.execute("DELETE FROM category WHERE id=?", (category_id,))
        conn.execute(
            "DELETE FROM category_merge WHERE source_id=? OR target_id=?", (category_id, category_id)
        )
        return paths

    return db.write(_write)


def merge_categories(db, source_id, target_id):
    """来源并到目标：参考图搜过去、来源删掉、记一条别名让历史随访。"""

    def _write(conn):
        conn.execute("UPDATE reference SET category_id=? WHERE category_id=?", (target_id, source_id))
        conn.execute("UPDATE category_merge SET target_id=? WHERE target_id=?", (target_id, source_id))
        conn.execute("DELETE FROM category WHERE id=?", (source_id,))
        conn.execute(
            "INSERT INTO category_merge(source_id, target_id) VALUES(?, ?)"
            " ON CONFLICT(source_id) DO UPDATE SET target_id=excluded.target_id",
            (source_id, target_id),
        )

    return db.write(_write)


def category_aliases(db):
    """{已删来源 id: 最终目标 id}。目标本身也被删了的，不再返回——那就是未识别。"""
    rows = db.conn.execute("SELECT source_id, target_id FROM category_merge").fetchall()
    direct = {row["source_id"]: row["target_id"] for row in rows}
    alive = {row["id"] for row in db.conn.execute("SELECT id FROM category").fetchall()}

    result = {}
    for source, target in direct.items():
        for _ in range(len(direct) + 1):
            if target in alive or target not in direct:
                break
            target = direct[target]
        if target in alive:
            result[str(source)] = target
    return result
