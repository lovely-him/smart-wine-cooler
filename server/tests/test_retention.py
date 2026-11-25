"""保留与回收：删到剩多少条、空间不够删多旧、被打断的残渣怎么清。"""

import pytest

from server import retention
from server.db import Database, repo


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "app.db")
    db.init_schema()
    images = tmp_path / "images"
    images.mkdir()

    ids = []
    for _ in range(5):
        observation_id = repo.save_observation(
            db, status="done", trigger="manual", started_at="2026-11-22T10:00:00+08:00"
        )
        folder = images / str(observation_id)
        folder.mkdir()
        (folder / "layer1.jpg").write_bytes(b"x")
        ids.append(observation_id)

    yield db, images, ids
    db.close()


def remaining(db):
    return sorted(row["id"] for row in db.conn.execute("SELECT id FROM observation"))


def test_trim_keeps_newest(env):
    db, images, ids = env

    assert retention.trim(db, images, 2) == 3
    assert remaining(db) == ids[-2:]
    assert not (images / str(ids[0])).exists()
    assert (images / str(ids[-1])).exists()


def test_trim_does_nothing_when_under_limit(env):
    db, images, ids = env

    assert retention.trim(db, images, 9) == 0
    assert remaining(db) == ids


def test_trim_zero_means_unlimited(env):
    db, images, _ = env

    assert retention.trim(db, images, 0) == 0
    assert len(remaining(db)) == 5


def test_reclaim_stops_once_space_is_back(env):
    db, images, ids = env
    calls = {"n": 0}

    def low():
        calls["n"] += 1
        return calls["n"] <= 2

    assert retention.reclaim(db, images, low) == 2
    assert remaining(db) == ids[2:]


def test_reclaim_gives_up_when_nothing_left(env):
    db, images, _ = env

    assert retention.reclaim(db, images, lambda: True) == 5
    assert remaining(db) == []


def test_orphan_folders_are_dropped(env):
    db, images, ids = env
    leftover = images / "999"
    leftover.mkdir()
    (leftover / "layer1.jpg").write_bytes(b"x")
    (images / "not-a-number").mkdir()

    assert retention.reclaim_orphans(db, images) == 1
    assert not leftover.exists()
    assert (images / "not-a-number").is_dir()
    assert all((images / str(item)).is_dir() for item in ids)
