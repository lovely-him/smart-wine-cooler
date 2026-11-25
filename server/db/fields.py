"""类别详情表单的字段定义。

两级并存：`category_id` 为空的是全局模板（影响所有类别），否则只属于那一个类别。
两级有同名字段时**类别自己的覆盖全局的**——这是"我只想给这瓶多加一栏"的表达方式。

删字段只删定义，不删已填的值：值留在类别的 details 里，只是不再显示。
哪天把同名字段加回来，值自动恢复。
"""

from server.models.fields import DETAIL_FIELDS


def _row(row):
    return {
        "id": row["id"],
        "key": row["key"],
        "label": row["label"],
        "askable": bool(row["askable"]),
        "categoryId": row["category_id"],
    }


def ensure_seeded(db):
    """空库时把出厂模板灌进全局字段。已经有字段就不动，改过的模板不会被覆盖。"""
    if db.conn.execute("SELECT COUNT(*) FROM field_def WHERE category_id IS NULL").fetchone()[0]:
        return 0

    def _write(conn):
        for position, item in enumerate(DETAIL_FIELDS):
            conn.execute(
                "INSERT INTO field_def(category_id, key, label, askable, position)"
                " VALUES(NULL, ?, ?, ?, ?)",
                (item.key, item.label, 1 if item.askable else 0, position),
            )
        return len(DETAIL_FIELDS)

    return db.write(_write)


def list_fields(db, category_id=None):
    """全局 + 该类别自己的；同 key 以类别自己的为准，顺序沿用全局位置。"""
    rows = db.conn.execute(
        "SELECT * FROM field_def WHERE category_id IS NULL OR category_id=? ORDER BY position, id",
        (category_id,),
    ).fetchall()
    merged = {row["key"]: _row(row) for row in rows if row["category_id"] is None}
    for row in rows:
        if row["category_id"] is not None:
            merged[row["key"]] = _row(row)
    return list(merged.values())


def get_field(db, field_id):
    row = db.conn.execute("SELECT * FROM field_def WHERE id=?", (field_id,)).fetchone()
    return _row(row) if row else None


def create_field(db, key, label, askable=True, category_id=None):
    def _write(conn):
        taken = conn.execute(
            "SELECT 1 FROM field_def WHERE key=? AND IFNULL(category_id, 0)=?",
            (key, category_id or 0),
        ).fetchone()
        if taken:
            raise KeyTaken(key)
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM field_def WHERE IFNULL(category_id, 0)=?",
            (category_id or 0,),
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO field_def(category_id, key, label, askable, position)"
            " VALUES(?, ?, ?, ?, ?)",
            (category_id, key, label, 1 if askable else 0, position),
        )
        return cur.lastrowid

    return db.write(_write)


def update_field(db, field_id, label, askable):
    def _write(conn):
        conn.execute(
            "UPDATE field_def SET label=?, askable=? WHERE id=?",
            (label, 1 if askable else 0, field_id),
        )
        return field_id

    return db.write(_write)


def delete_field(db, field_id):
    def _write(conn):
        conn.execute("DELETE FROM field_def WHERE id=?", (field_id,))
        return field_id

    return db.write(_write)


class KeyTaken(Exception):
    """同一级里 key 已经存在。"""
