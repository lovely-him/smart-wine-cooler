"""提交：落核对状态、补人工新增那件的裁剪图、把确认过的图收进参考库。"""

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from server.db import Database, checkpoints, repo
from server.infer.classifier import ReferenceIndex
from server.models.api import CommitEntity, CommitLayer, CommitRequest
from server.models.objects import Box
from server.pipeline import commit as commit_pipeline
from server.pipeline import scan


class StubDetector:
    def detect(self, img):
        return [
            {"score": 0.9, "box": [0.1, 0.2, 0.3, 0.6]},
            {"score": 0.8, "box": [0.5, 0.2, 0.8, 0.6]},
        ]


class StubClassifier:
    pad = 0.06

    def __init__(self):
        self.index = ReferenceIndex()
        self.calls = 0

    def classify(self, img):
        return {"category_id": None, "category": None, "reason": "参考库为空", "best_score": None}

    def feature(self, img):
        self.calls += 1
        return np.full(8, self.calls, dtype=np.float32) / np.sqrt(8 * self.calls)


def textured(size=(320, 240)):
    rng = np.random.default_rng(5)
    return Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8), "RGB")


@pytest.fixture
def runtime(tmp_path):
    good = tmp_path / "good"
    images = tmp_path / "images"
    refs = tmp_path / "refs"
    for folder in (good, images, refs):
        folder.mkdir()
    textured().save(good / "frame.jpg")

    db = Database(tmp_path / "app.db")
    db.init_schema()
    camera = SimpleNamespace(
        id="cam-1",
        label="摄像头 1",
        backend="image_dir",
        width=320,
        height=240,
        rotate=0,
        directory=str(good),
    )
    runtime = SimpleNamespace(
        db=db,
        image_dir=images,
        reference_dir=refs,
        config={"judge.ref_dedupe": 0.95, "judge.ref_cap": 12},
        classifier=StubClassifier(),
        cameras=[camera],
        layers=[SimpleNamespace(id="layer1", name="第一层", camera=camera.id)],
        grab_cfg={"skip": 0, "batch": 3},
        detector=StubDetector(),
        storage_low=lambda: False,
        warmup_seconds=0,
    )
    runtime.camera_of = lambda layer: camera
    runtime.stability_cfg = lambda layer: {
        "stable_frames": 3,
        "settle_timeout": 0.5,
        "grab_batch": 3,
        "diff_thresh": 1.5,
        "min_bright": 15,
        "max_bright": 245,
        "min_sharp": 20,
        "rotate": 0,
    }
    runtime.reload_references = lambda: None
    yield runtime
    db.close()


def observe_once(runtime):
    obs_id, status = scan.run_scan(runtime, "manual")
    assert status == "done"
    return obs_id


def test_commit_writes_checkpoint_with_images(runtime):
    obs_id = observe_once(runtime)
    observation = repo.load_observation(runtime.db, obs_id)
    entities = observation["layers"][0]["entities"]

    body = CommitRequest(
        base_observation_id=obs_id,
        layers=[
            CommitLayer(
                layer_id="layer1",
                entities=[
                    CommitEntity(id=item["id"], box=Box(**item["box"]), category_id=None)
                    for item in entities
                ],
            )
        ],
    )
    checkpoint_id = commit_pipeline.commit(runtime, body)

    saved = checkpoints.latest_checkpoint(runtime.db)
    assert saved["id"] == checkpoint_id
    assert saved["based_on_observation"] == obs_id
    assert len(saved["submitted"]) == 1
    assert len(saved["submitted"][0]["entities"]) == len(entities)
    assert saved["submitted"][0]["entities"][0]["cropUrl"] == entities[0]["crop_url"]
    assert saved["reported"][0]["imageUrl"] == observation["layers"][0]["image_url"]


def test_manual_entity_gets_crop_generated(runtime):
    obs_id = observe_once(runtime)
    body = CommitRequest(
        base_observation_id=obs_id,
        layers=[
            CommitLayer(
                layer_id="layer1",
                entities=[CommitEntity(id=None, box=Box(x=0.4, y=0.1, w=0.2, h=0.3), category_id=None)],
            )
        ],
    )
    commit_pipeline.commit(runtime, body)

    entity = checkpoints.latest_checkpoint(runtime.db)["submitted"][0]["entities"][0]
    assert entity["id"].endswith("add0")
    assert entity["cropUrl"]
    assert (runtime.image_dir / entity["cropUrl"]).is_file()


def test_confirmed_entities_enter_reference_library(runtime):
    category_id = repo.create_category(runtime.db, "示例类别")
    obs_id = observe_once(runtime)
    entities = repo.load_observation(runtime.db, obs_id)["layers"][0]["entities"]

    body = CommitRequest(
        base_observation_id=obs_id,
        layers=[
            CommitLayer(
                layer_id="layer1",
                entities=[
                    CommitEntity(id=item["id"], box=Box(**item["box"]), category_id=category_id)
                    for item in entities
                ],
            )
        ],
    )
    commit_pipeline.commit(runtime, body)

    groups = repo.reference_groups(runtime.db)
    assert groups
    assert groups[0]["category_id"] == category_id
    assert len(list((runtime.reference_dir / str(category_id)).iterdir())) == len(groups)


def test_missing_base_observation_rejected(runtime):
    body = CommitRequest(base_observation_id=999, layers=[CommitLayer(layer_id="layer1")])
    with pytest.raises(ValueError):
        commit_pipeline.commit(runtime, body)


def test_stale_base_rejected(runtime):
    first = observe_once(runtime)
    newer = observe_once(runtime)
    body = CommitRequest(base_observation_id=first, layers=[CommitLayer(layer_id="layer1")])
    with pytest.raises(commit_pipeline.ConflictError) as caught:
        commit_pipeline.commit(runtime, body)
    assert caught.value.latest_observation_id == newer


def test_latest_base_accepted(runtime):
    observe_once(runtime)
    newest = observe_once(runtime)
    body = CommitRequest(base_observation_id=newest, layers=[CommitLayer(layer_id="layer1")])
    commit_pipeline.commit(runtime, body)
    assert checkpoints.latest_checkpoint(runtime.db)["based_on_observation"] == newest


def test_void_observation_does_not_block_commit(runtime):
    obs_id = observe_once(runtime)
    repo.save_observation(
        runtime.db,
        status="void",
        trigger="door",
        started_at="2026-11-22T10:00:00+08:00",
        void_reason="第一层：门被打开，这次不算",
    )
    body = CommitRequest(base_observation_id=obs_id, layers=[CommitLayer(layer_id="layer1")])
    commit_pipeline.commit(runtime, body)
    assert checkpoints.latest_checkpoint(runtime.db)["based_on_observation"] == obs_id


def test_commit_does_not_change_observation(runtime):
    obs_id = observe_once(runtime)
    before = repo.load_observation(runtime.db, obs_id)
    body = CommitRequest(
        base_observation_id=obs_id,
        layers=[CommitLayer(layer_id="layer1", entities=[])],
    )
    commit_pipeline.commit(runtime, body)
    assert repo.load_observation(runtime.db, obs_id) == before


def test_position_history_merges_repeats_and_marks_gaps(runtime):
    alpha = repo.create_category(runtime.db, "甲")
    obs_id = observe_once(runtime)
    observation = repo.load_observation(runtime.db, obs_id)
    entities = observation["layers"][0]["entities"]

    def commit(entities_in_order):
        return commit_pipeline.commit(
            runtime,
            CommitRequest(
                base_observation_id=obs_id,
                layers=[CommitLayer(layer_id="layer1", entities=entities_in_order)],
            ),
        )

    def entity(source, category_id=None):
        return CommitEntity(id=source["id"], box=Box(**source["box"]), category_id=category_id)

    first = commit([entity(entities[0], alpha), entity(entities[1], alpha)])
    commit([entity(entities[0], alpha), entity(entities[1], alpha)])
    commit([entity(entities[0], None), entity(entities[1])])
    commit([entity(entities[0])])

    ids = [item["checkpointId"] for item in checkpoints.position_history(runtime.db, "layer1", 1)]
    assert first not in ids

    second_slot = checkpoints.position_history(runtime.db, "layer1", 1)
    assert [item["state"] for item in second_slot] == ["empty", "unknown", "known"]

    assert checkpoints.position_history(runtime.db, "layer2", 0) == []
