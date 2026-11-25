"""建库、落库、读回：观察写入后必须能原样取出来。"""

import pytest
import sqlite3

from server.db import SCHEMA_VERSION, Database, checkpoints, repo, sequence


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    yield database
    database.close()


def test_schema_version_readable(db):
    assert db.schema_version() == SCHEMA_VERSION


def test_init_schema_is_idempotent(db):
    db.init_schema()
    db.init_schema()
    assert db.schema_version() == SCHEMA_VERSION


def test_sequence_is_shared_across_tables(db):
    first = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    checkpoint_id = checkpoints.save_checkpoint(db, first, [], [], note="")
    change_id = checkpoints.save_change(db, first, [])
    second = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:01:00+08:00"
    )

    assert [first, checkpoint_id, change_id, second] == [1, 2, 3, 4]
    assert sequence.current(db.conn) == 4


def test_observation_roundtrip(db):
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00",
        finished_at="2026-11-22T10:00:07+08:00",
    )
    repo.save_layer_record(
        db, obs_id, "layer1", "第一层", usable=True, reason="",
        image_path="images/1/layer1.jpg",
        entities=[
            {
                "id": "e1",
                "box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
                "category_id": 7,
                "crop_path": "images/1/e1.jpg",
                "score": 0.91,
                "match_score": 0.82,
            }
        ],
    )

    loaded = repo.load_observation(db, obs_id)
    assert loaded["status"] == "done"
    assert loaded["trigger"] == "door"
    assert len(loaded["layers"]) == 1

    layer = loaded["layers"][0]
    assert layer["layer_id"] == "layer1"
    assert layer["usable"] is True
    assert layer["image_url"] == "images/1/layer1.jpg"

    entity = layer["entities"][0]
    assert entity["id"] == "e1"
    assert entity["box"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
    assert entity["category_id"] == 7
    assert entity["score"] == 0.91
    assert entity["match_score"] == 0.82


def test_void_observation_kept(db):
    obs_id = repo.save_observation(
        db, status="void", trigger="door", started_at="2026-11-22T10:00:00+08:00",
        void_reason="第 2 层画面一直没静止",
    )
    loaded = repo.load_observation(db, obs_id)
    assert loaded["status"] == "void"
    assert "没静止" in loaded["void_reason"]


def test_latest_and_paging(db):
    ids = [
        repo.save_observation(db, status="done", trigger="timer", started_at=f"2026-11-22T1{i}:00:00+08:00")
        for i in range(3)
    ]

    assert repo.latest_observation(db)["id"] == ids[-1]
    assert [o["id"] for o in repo.list_observations(db, limit=2)] == [ids[-1], ids[-2]]
    assert [o["id"] for o in repo.list_observations(db, before_id=ids[-1], limit=2)] == [
        ids[-2],
        ids[-3],
    ]


def test_category_and_reference(db):
    import numpy as np

    category_id = repo.create_category(db, "示例类别", shape="bordeaux", details={"品牌": "某牌"})
    assert repo.list_categories(db)[0]["details"]["品牌"] == "某牌"

    vector = np.ones(8, dtype=np.float32) / np.sqrt(8)
    repo.add_reference(db, category_id, "images/ref/1.jpg", vector, 8)

    groups = repo.reference_groups(db)
    assert len(groups) == 1
    assert groups[0]["category_id"] == category_id
    assert groups[0]["name"] == "示例类别"
    assert np.allclose(groups[0]["vector"], vector)


def test_observation_never_updated(db):
    obs_id = repo.save_observation(db, status="done", trigger="manual", started_at="2026-11-22T10:00:00+08:00")
    assert not hasattr(repo, "update_observation")
    assert repo.load_observation(db, obs_id)["status"] == "done"


def test_delete_observation_cascades_layers_and_changes(db):
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    repo.save_layer_record(db, obs_id, "layer1", "第一层", True, "", "", [])
    checkpoints.save_change(db, obs_id, [])

    repo.delete_observations(db, [obs_id])

    assert repo.list_observations(db) == []
    assert checkpoints.list_changes(db) == []


def test_delete_checkpoint_keeps_observation(db):
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    checkpoint_id = checkpoints.save_checkpoint(db, obs_id, [], [])

    checkpoints.delete_checkpoints(db, [checkpoint_id])

    assert checkpoints.get_checkpoint(db, checkpoint_id) is None
    assert repo.load_observation(db, obs_id) is not None


def test_migration_adds_layer_name_without_losing_data(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    db.init_schema()
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    repo.save_layer_record(db, obs_id, "layer1", "第一层", True, "", "", [])
    db.close()

    raw = sqlite3.connect(str(path))
    raw.execute("ALTER TABLE layer_record DROP COLUMN layer_name")
    raw.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    raw.commit()
    raw.close()

    old = Database(path)
    try:
        old.init_schema()
        assert old.schema_version() == 4
        rows = old.conn.execute("SELECT layer_id, layer_name FROM layer_record").fetchall()
        assert [tuple(row) for row in rows] == [("layer1", "")]
    finally:
        old.close()


def test_position_history_counts_by_coordinates(db):
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    left = {"x": 0.10, "y": 0.40, "w": 0.10, "h": 0.20}
    right = {"x": 0.60, "y": 0.40, "w": 0.10, "h": 0.20}
    checkpoints.save_checkpoint(
        db,
        based_on_observation=obs_id,
        reported=[],
        submitted=[
            {
                "layerId": "L1",
                "imageUrl": None,
                "entities": [
                    {"id": "right", "box": right, "categoryId": 9, "cropUrl": None},
                    {"id": "left", "box": left, "categoryId": 7, "cropUrl": None},
                ],
            }
        ],
        note="",
    )

    assert checkpoints.position_history(db, "L1", 0)[0]["categoryId"] == 7
    assert checkpoints.position_history(db, "L1", 1)[0]["categoryId"] == 9
    assert checkpoints.position_history(db, "L1", 2)[0]["state"] == "empty"


def test_classify_history_ids_separates_events(db):
    obs_id = repo.save_observation(
        db, status="done", trigger="door", started_at="2026-11-22T10:00:00+08:00"
    )
    checkpoint_id = checkpoints.save_checkpoint(
        db,
        based_on_observation=obs_id,
        reported={"layers": []},
        submitted={"layers": []},
        note="",
    )
    change_id = sequence.next_id(db.conn)
    db.write(
        lambda conn: conn.execute(
            "INSERT INTO change_record(id, observation_id, items, created_at)"
            " VALUES(?, ?, '[]', '2026-11-22T10:00:00+08:00')",
            (change_id, obs_id),
        )
    )

    observation_ids, checkpoint_ids, unknown = repo.classify_history_ids(
        db, [obs_id, checkpoint_id, change_id, 99999]
    )

    assert observation_ids == [obs_id]
    assert checkpoint_ids == [checkpoint_id]
    assert unknown == [change_id, 99999]
