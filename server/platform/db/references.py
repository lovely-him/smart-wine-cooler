"""参考图：属于某个类别的一批裁剪图特征。

只被「提交」写入，人也能删；读出来是给分类器建内存索引用的。
"""

from . import now


def delete_reference(db, reference_id):
    """删一张参考图，返回它的相对路径（文件要跟着删）。"""

    def _write(conn):
        row = conn.execute("SELECT path FROM reference WHERE id=?", (reference_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM reference WHERE id=?", (reference_id,))
        return row["path"]

    return db.write(_write)


def add_reference(db, category_id, path, feature, dim):
    """特征统一按 float32 存。

    不强制转换的话，一个 float64 的向量会存成两倍字节数，读回来维度对不上、
    被静默丢弃，表现为「参考图明明入库了却检索不到」。
    """
    import numpy as np

    blob = np.asarray(feature, dtype=np.float32).reshape(-1).tobytes()

    def _write(conn):
        cur = conn.execute(
            "INSERT INTO reference(category_id, path, feature, dim, created_at) VALUES(?, ?, ?, ?, ?)",
            (category_id, path, blob, int(dim), now()),
        )
        return cur.lastrowid

    return db.write(_write)


def reference_groups(db):
    """[{category_id, name, vector}]，供分类器建内存索引。"""
    import numpy as np

    rows = db.conn.execute(
        "SELECT c.id AS cid, c.name AS name, r.feature AS feature, r.dim AS dim"
        " FROM reference r JOIN category c ON c.id = r.category_id ORDER BY r.id"
    ).fetchall()
    groups = []
    for row in rows:
        vector = np.frombuffer(row["feature"], dtype=np.float32)
        if vector.size == row["dim"]:
            groups.append({"category_id": row["cid"], "name": row["name"], "vector": vector})
    return groups
