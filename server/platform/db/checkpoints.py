"""核对状态与变化记录的读写。"""

import json

from server.kernel import ordering

from . import now, sequence


def save_checkpoint(db, based_on_observation, reported, submitted, note=""):
    def _write(conn):
        record_id = sequence.next_id(conn)
        conn.execute(
            "INSERT INTO checkpoint(id, based_on_observation, reported, submitted, note, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (
                record_id,
                based_on_observation,
                json.dumps(reported, ensure_ascii=False),
                json.dumps(submitted, ensure_ascii=False),
                note,
                now(),
            ),
        )
        return record_id

    return db.write(_write)


def _hydrate(row):
    return {
        "id": row["id"],
        "based_on_observation": row["based_on_observation"],
        "reported": json.loads(row["reported"]),
        "submitted": json.loads(row["submitted"]),
        "note": row["note"],
        "created_at": row["created_at"],
    }


def latest_checkpoint(db):
    row = db.conn.execute("SELECT * FROM checkpoint ORDER BY id DESC LIMIT 1").fetchone()
    return _hydrate(row) if row else None


def get_checkpoint(db, checkpoint_id):
    row = db.conn.execute("SELECT * FROM checkpoint WHERE id=?", (checkpoint_id,)).fetchone()
    return _hydrate(row) if row else None


def delete_checkpoints(db, ids):
    if not ids:
        return

    def _write(conn):
        conn.executemany("DELETE FROM checkpoint WHERE id=?", [(item,) for item in ids])

    db.write(_write)


def list_checkpoints(db, before_id=None, limit=20):
    if before_id:
        rows = db.conn.execute(
            "SELECT * FROM checkpoint WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)
        ).fetchall()
    else:
        rows = db.conn.execute("SELECT * FROM checkpoint ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_hydrate(row) for row in rows]


def position_history(db, layer_id, index, limit=200):
    """某个位置上先后放过什么。

    位置是「框坐标 + 层」现算的投影，所以只认确认记录，不看观察；
    第几个按「框中心 x，再中心 y」从左到右数。连续的相同状态合并成一条，历史才读得动。
    """
    rows = db.conn.execute(
        "SELECT * FROM checkpoint ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

    entries = []
    previous = None
    for row in rows:
        record = _hydrate(row)
        layer = next((item for item in record["submitted"] if item["layerId"] == layer_id), None)
        if layer is None:
            continue
        entities = sorted(
            layer.get("entities", []), key=lambda item: ordering.key_xywh(item["box"])
        )
        state = "empty" if index >= len(entities) else entities[index].get("categoryId")
        if state == previous:
            continue
        previous = state
        entries.append(
            {
                "checkpointId": record["id"],
                "at": record["created_at"],
                "categoryId": state if isinstance(state, int) else None,
                "state": "empty" if state == "empty" else ("unknown" if state is None else "known"),
            }
        )
    return entries


def save_change(db, observation_id, items):
    def _write(conn):
        record_id = sequence.next_id(conn)
        conn.execute(
            "INSERT INTO change_record(id, observation_id, items, created_at) VALUES(?, ?, ?, ?)",
            (record_id, observation_id, json.dumps(items, ensure_ascii=False), now()),
        )
        return record_id

    return db.write(_write)


def list_changes(db, before_id=None, limit=20):
    if before_id:
        rows = db.conn.execute(
            "SELECT * FROM change_record WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT * FROM change_record ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "id": row["id"],
            "observation_id": row["observation_id"],
            "at": row["created_at"],
            "items": json.loads(row["items"]),
        }
        for row in rows
    ]
