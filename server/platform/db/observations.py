"""观察、层记录、实体的读写。

只做「存进去、原样取出来」，不做业务判断——作废、去抖、差异都在上层。
这三样只追加：这里没有任何更新或修改已落库内容的入口。
"""

from datetime import datetime

from . import sequence


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
