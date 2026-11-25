"""观察记录的保留与回收。

图片没有独立身份：它随观察产生，也随观察消失。所以回收的粒度永远是**一次观察**——
删记录连图一起删，不存在"只删图留记录"或"只删记录留图"。
"""

import shutil
from pathlib import Path

from server.db import repo


def oldest(db, count):
    rows = db.conn.execute("SELECT id FROM observation ORDER BY id LIMIT ?", (count,)).fetchall()
    return [row["id"] for row in rows]


def drop(db, image_dir, ids):
    if not ids:
        return 0
    repo.delete_observations(db, ids)
    for observation_id in ids:
        shutil.rmtree(Path(image_dir) / str(observation_id), ignore_errors=True)
    return len(ids)


def trim(db, image_dir, keep):
    """超过上限就从最旧的删，返回删掉的条数。"""
    if keep <= 0:
        return 0
    total = db.conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    if total <= keep:
        return 0
    return drop(db, image_dir, oldest(db, total - keep))


def reclaim(db, image_dir, low):
    """空间不够时一次删一条最旧的，直到 low() 说不缺了或没有可删的。"""
    dropped = 0
    while low():
        ids = oldest(db, 1)
        if not ids:
            break
        dropped += drop(db, image_dir, ids)
    return dropped


def reclaim_orphans(db, image_dir):
    """没有观察记录指向的图片目录是上次被打断留下的残渣。"""
    known = {row["id"] for row in db.conn.execute("SELECT id FROM observation")}
    dropped = 0
    for folder in sorted(Path(image_dir).iterdir()):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        if int(folder.name) in known:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        dropped += 1
    return dropped
