"""读写落库对象。

只做「把对象存进去、原样取出来」，不做业务判断——差异、去抖、作废规则都在上层。
观察与实体只追加，这里不提供任何 update/delete 观察的入口。
"""

import json
import sqlite3
from datetime import datetime

from . import sequence


class NameTaken(Exception):
    """类别名已存在。名字在库里是唯一的。"""


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save_observation(db, status, trigger, started_at, finished_at="", void_reason=""):
    def _write(conn):
        record_id = sequence.next_id(conn)
        conn.execute(
            "INSERT INTO observation(id, status, void_reason, trigger, started_at, finished_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (record_id, status, void_reason, trigger, started_at, finished_at),
        )
        return record_id

    return db.write(_write)


def save_layer_record(
    db, observation_id, layer_id, layer_name, usable, reason, image_path, entities
):
    """写入一层及其实体。entities 里每项含 id/box/category_id/crop_path/score/match_score。"""

    def _write(conn):
        cur = conn.execute(
            "INSERT INTO layer_record(observation_id, layer_id, layer_name, usable, reason, image_path)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (observation_id, layer_id, layer_name, 1 if usable else 0, reason, image_path),
        )
        layer_record_id = cur.lastrowid
        for item in entities:
            box = item["box"]
            conn.execute(
                "INSERT INTO entity(id, layer_record_id, x, y, w, h, category_id, crop_path, score, match_score)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["id"],
                    layer_record_id,
                    box["x"],
                    box["y"],
                    box["w"],
                    box["h"],
                    item.get("category_id"),
                    item.get("crop_path", ""),
                    item.get("score"),
                    item.get("match_score"),
                ),
            )
        return layer_record_id

    return db.write(_write)


def _row_to_entity(row):
    return {
        "id": row["id"],
        "box": {"x": row["x"], "y": row["y"], "w": row["w"], "h": row["h"]},
        "category_id": row["category_id"],
        "crop_url": row["crop_path"] or None,
        "score": row["score"],
        "match_score": row["match_score"],
    }


def load_observation(db, observation_id):
    row = db.conn.execute("SELECT * FROM observation WHERE id=?", (observation_id,)).fetchone()
    if row is None:
        return None
    return _hydrate(db, row)


def latest_observation(db):
    row = db.conn.execute("SELECT * FROM observation ORDER BY id DESC LIMIT 1").fetchone()
    return _hydrate(db, row) if row else None


def latest_done_observation(db):
    """最近一次**成立**的观察。作废的不算——它没有产出可用的画面。"""
    row = db.conn.execute(
        "SELECT * FROM observation WHERE status='done' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _hydrate(db, row) if row else None


def previous_observation(db, observation_id):
    """上一次**成立**的观察。作废的那些不参与比较。"""
    row = db.conn.execute(
        "SELECT * FROM observation WHERE id < ? AND status='done' ORDER BY id DESC LIMIT 1",
        (observation_id,),
    ).fetchone()
    return _hydrate(db, row) if row else None


def observations_before(db, cutoff):
    """开始时间早于 cutoff 的观察 id。字符串时间戳先解析再比，避免时区与格式坑。"""
    result = []
    for row in db.conn.execute("SELECT id, started_at FROM observation ORDER BY id").fetchall():
        try:
            started = datetime.fromisoformat(row["started_at"])
        except ValueError:
            continue
        if started < cutoff:
            result.append(row["id"])
    return result


def delete_observations(db, ids):
    """硬删观察。层记录与变化记录由外键级联删除。"""
    if not ids:
        return

    def _write(conn):
        conn.executemany("DELETE FROM observation WHERE id=?", [(item,) for item in ids])

    db.write(_write)


def classify_history_ids(db, ids):
    """把历史 id 分成（观察、确认、认不出的）。事件没有独立身份，认不出来就是它。"""
    from . import checkpoints

    observation_ids = []
    checkpoint_ids = []
    unknown = []
    for entry_id in ids:
        if load_observation(db, entry_id) is not None:
            observation_ids.append(entry_id)
        elif checkpoints.get_checkpoint(db, entry_id) is not None:
            checkpoint_ids.append(entry_id)
        else:
            unknown.append(entry_id)
    return observation_ids, checkpoint_ids, unknown


def used_layers(db):
    """历史里出现过的层。删掉的层也要能查得到它的历史，所以不能只列当前配置。"""
    rows = db.conn.execute(
        "SELECT layer_id, layer_name FROM layer_record ORDER BY id DESC"
    ).fetchall()
    seen = {}
    for row in rows:
        entry = seen.setdefault(row["layer_id"], {"id": row["layer_id"], "name": ""})
        if not entry["name"] and row["layer_name"]:
            entry["name"] = row["layer_name"]
    for entry in seen.values():
        entry["name"] = entry["name"] or entry["id"]
    return sorted(seen.values(), key=lambda item: item["id"])


def list_observations(db, before_id=None, limit=20):
    if before_id:
        rows = db.conn.execute(
            "SELECT * FROM observation WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT * FROM observation ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_hydrate(db, row) for row in rows]


def _hydrate(db, obs_row):
    obs_id = obs_row["id"]
    layers = []
    for layer in db.conn.execute(
        "SELECT * FROM layer_record WHERE observation_id=? ORDER BY id", (obs_id,)
    ).fetchall():
        entities = [
            _row_to_entity(row)
            for row in db.conn.execute(
                "SELECT * FROM entity WHERE layer_record_id=? ORDER BY rowid", (layer["id"],)
            ).fetchall()
        ]
        layers.append(
            {
                "layer_id": layer["layer_id"],
                "layer_name": layer["layer_name"],
                "usable": bool(layer["usable"]),
                "reason": layer["reason"],
                "image_url": layer["image_path"] or None,
                "entities": entities,
            }
        )
    return {
        "id": obs_id,
        "status": obs_row["status"],
        "void_reason": obs_row["void_reason"],
        "trigger": obs_row["trigger"],
        "started_at": obs_row["started_at"],
        "finished_at": obs_row["finished_at"] or None,
        "layers": layers,
    }


def create_category(db, name, shape="", details=None):
    def _write(conn):
        cur = conn.execute(
            "INSERT INTO category(name, shape, details, created_at) VALUES(?, ?, ?, ?)",
            (name, shape, json.dumps(details or {}, ensure_ascii=False), _now()),
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
            (category_id, path, blob, int(dim), _now()),
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
