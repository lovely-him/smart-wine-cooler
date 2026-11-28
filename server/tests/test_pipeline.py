"""整柜观察与触发调度。

用图片目录当采集源、用替身检测器，不碰摄像头和 NPU——验的是编排逻辑：
全层成功才成立、任一层失败整次作废、门开关怎么影响观察。
"""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from server.platform.layers import CameraConfig, LayerConfig
from server.platform.db import Database, observations
from server.platform.events import EventBus
from server.platform.door import CLOSED, OPENED, MockDoor
from server.observe.runner import ObserveRunner
from server.observe import scan
from server.observe.scheduler import Scheduler, blocked_reason
from server.platform.hardware import Hardware


class StubDetector:
    def __init__(self, counts=(2, 2)):
        self.counts = list(counts)
        self._call = 0

    def detect(self, img):
        count = self.counts[min(self._call, len(self.counts) - 1)]
        self._call += 1
        boxes = [[0.1, 0.2, 0.3, 0.6], [0.5, 0.2, 0.8, 0.6]]
        return [{"score": 0.9, "box": box} for box in boxes[:count]]


class StubClassifier:
    pad = 0.06

    def __init__(self, category_id=None):
        self.category_id = category_id

    def classify(self, img):
        return {
            "category_id": self.category_id,
            "category": None,
            "reason": "ok" if self.category_id else "参考库为空",
            "best_score": 0.9 if self.category_id else None,
        }


def textured(size=(320, 240)):
    rng = np.random.default_rng(3)
    return Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8), "RGB")


def dark(size=(320, 240)):
    return Image.new("RGB", size, (4, 4, 4))


@pytest.fixture
def workdir(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    images = tmp_path / "images"
    for folder in (good, bad, images):
        folder.mkdir()
    textured().save(good / "frame.jpg")
    dark().save(bad / "frame.jpg")
    return SimpleNamespace(good=good, bad=bad, images=images, root=tmp_path)


def make_runtime(workdir, pairs, category_id=None):
    db = Database(workdir.root / "app.db")
    db.init_schema()
    cameras = {camera.id: camera for camera, _ in pairs}
    runtime = SimpleNamespace(
        db=db,
        image_dir=workdir.images,
        plan=SimpleNamespace(
            layers=[layer for _, layer in pairs],
            grab_cfg={"skip": 0, "batch": 3},
            warmup_seconds=0,
        ),
        engines=SimpleNamespace(detector=StubDetector(), classifier=StubClassifier(category_id)),
        housekeeping=SimpleNamespace(
            storage_low=lambda: False, reclaim_space=lambda: 0, trim=lambda: None
        ),
    )
    runtime.plan.camera_of = lambda layer: cameras[layer.camera]
    runtime.plan.stability_cfg = lambda layer: {
        "stable_frames": 3,
        "settle_timeout": 0.5,
        "grab_batch": 3,
        "diff_thresh": 1.5,
        "min_bright": 15,
        "max_bright": 245,
        "min_sharp": 20,
        "rotate": cameras[layer.camera].rotate,
    }
    return runtime


def run_scan(runtime, trigger, cancel=None):
    return scan.run_scan(
        db=runtime.db,
        image_dir=runtime.image_dir,
        plan=runtime.plan,
        engines=runtime.engines,
        housekeeping=runtime.housekeeping,
        trigger=trigger,
        cancel=cancel,
    )


def _db(tmp_path):
    db = Database(tmp_path / "scheduler.db")
    db.init_schema()
    return db


def _hardware(door):
    hardware = Hardware({}, EventBus())
    if door is not None:
        hardware.attach_door(door)
    return hardware


def image_layer(layer_id, folder):
    camera = CameraConfig(
        id=f"{layer_id}-cam",
        label=layer_id,
        backend="image_dir",
        width=320,
        height=240,
        rotate=0,
        directory=str(folder),
    )
    return camera, LayerConfig(id=layer_id, name=layer_id, camera=camera.id)


def test_scan_done_writes_layer_and_entities(workdir):
    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    obs_id, status = run_scan(runtime, "manual")

    assert status == "done"
    loaded = observations.load_observation(runtime.db, obs_id)
    assert loaded["status"] == "done"
    assert len(loaded["layers"]) == 1
    layer = loaded["layers"][0]
    assert layer["usable"] is True
    assert len(layer["entities"]) == 2
    assert layer["image_url"] == f"{obs_id}/layer1.jpg"
    assert layer["entities"][0]["box"]["w"] == pytest.approx(0.2, abs=1e-6)
    assert layer["entities"][1]["crop_url"].endswith("layer1-1.jpg")
    assert (workdir.images / str(obs_id) / "layer1.jpg").is_file()
    assert (workdir.images / str(obs_id) / "layer1-0.jpg").is_file()
    runtime.db.close()


def test_failed_layer_voids_whole_scan(workdir):
    runtime = make_runtime(
        workdir, [image_layer("layer1", workdir.good), image_layer("layer2", workdir.bad)]
    )
    obs_id, status = run_scan(runtime, "door")

    assert status == "void"
    loaded = observations.load_observation(runtime.db, obs_id)
    assert loaded["status"] == "void"
    assert "layer2" in loaded["void_reason"]
    assert any("亮度" in item for item in [loaded["void_reason"]])

    done_layers = [layer for layer in loaded["layers"] if layer["usable"]]
    assert done_layers == []
    runtime.db.close()


def test_cancel_before_layer_voids(workdir):
    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    obs_id, status = run_scan(runtime, "door", cancel=lambda: True)

    assert status == "void"
    loaded = observations.load_observation(runtime.db, obs_id)
    assert "门被打开" in loaded["void_reason"]
    assert loaded["layers"] == []
    runtime.db.close()


def test_no_checkpoint_means_no_change_record(workdir):
    from server.platform.db import checkpoints, observations

    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    run_scan(runtime, "manual")
    assert checkpoints.list_changes(runtime.db) == []
    runtime.db.close()


def test_change_recorded_when_deviation_shifts(workdir):
    from server.platform.db import checkpoints, observations

    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)], category_id=7)
    runtime.engines.detector = StubDetector(counts=(2, 1))

    first, _ = run_scan(runtime, "manual")

    tally = [{"layer_id": "layer1", "counts": [{"category_id": 7, "count": 2}]}]
    checkpoints.save_checkpoint(runtime.db, first, tally, tally)

    # 第一次观察的偏差是 0，且没有上一次可比 → 不记
    assert checkpoints.list_changes(runtime.db) == []

    # 第二次少了一件 → 偏差从 0 变成 -1，该记一条
    run_scan(runtime, "manual")
    records = checkpoints.list_changes(runtime.db)
    assert len(records) == 1
    assert records[0]["items"] == [{"layer_id": "layer1", "category_id": 7, "delta": -1}]
    runtime.db.close()


def test_deviation_reads_counts_from_submitted_entities():
    submitted = [
        {
            "layerId": "layer1",
            "imageUrl": "/img/1/layer1.jpg",
            "entities": [
                {"id": "a", "box": {}, "categoryId": 7, "cropUrl": None},
                {"id": "b", "box": {}, "categoryId": 7, "cropUrl": None},
            ],
        }
    ]
    fewer = [{"layer_id": "layer1", "counts": [{"category_id": 7, "count": 1}]}]
    assert scan.deviation(submitted, fewer) == [
        {"layer_id": "layer1", "category_id": 7, "delta": -1}
    ]

    more = [{"layer_id": "layer1", "counts": [{"category_id": 7, "count": 3}]}]
    assert scan.deviation(submitted, more) == [
        {"layer_id": "layer1", "category_id": 7, "delta": 1}
    ]


def test_blocked_when_door_is_open():
    class DoorOpen:
        def door_state(self):
            return "open"

    class DoorClosed:
        def door_state(self):
            return "closed"

    class NoDoor:
        def door_state(self):
            return "unknown"

    assert blocked_reason(DoorOpen()) == "门打开着，等关上再观察"
    assert blocked_reason(DoorClosed()) is None
    assert blocked_reason(NoDoor()) is None


def test_scheduler_triggers_on_door_close(tmp_path):
    calls = []
    door = MockDoor()
    interrupted = threading.Event()
    db = _db(tmp_path)
    scheduler = Scheduler(
        config={"trig.timer_enabled": False},
        db=db,
        observe=SimpleNamespace(start_observe=calls.append, is_observing=lambda: False),
        hardware=_hardware(door),
        interrupted=interrupted,
    )

    door.set(OPENED)
    scheduler._tick()
    assert calls == []
    assert interrupted.is_set() is True

    door.set(CLOSED)
    scheduler._tick()
    assert calls == ["door"]
    assert interrupted.is_set() is False
    db.close()


def test_scheduler_ignores_close_while_observing(tmp_path):
    """门关照常上报，能不能开始由观察状态决定——调度器不自己判断。"""
    calls = []
    door = MockDoor()
    db = _db(tmp_path)
    scheduler = Scheduler(
        config={"trig.timer_enabled": False},
        db=db,
        observe=SimpleNamespace(start_observe=calls.append, is_observing=lambda: True),
        hardware=_hardware(door),
        interrupted=threading.Event(),
    )
    door.set(OPENED)
    scheduler._tick()
    door.set(CLOSED)
    scheduler._tick()
    assert calls == ["door"]
    db.close()


def test_door_close_during_scan_is_replayed_after():
    """正在观察时门关：这次不能丢，记下来等收尾后补跑。"""
    runner = ObserveRunner.__new__(ObserveRunner)
    runner._lock = threading.Lock()
    runner._observing = True
    runner._pending_trigger = None

    assert runner.start_observe("door") is False
    assert runner._pending_trigger == "door"

    assert runner.start_observe("manual") is False
    assert runner._pending_trigger == "door"


def test_runner_lights_up_for_the_whole_scan(workdir):
    ctx = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    hardware = _hardware(MockDoor())
    runner = ObserveRunner(
        db=ctx.db,
        image_dir=ctx.image_dir,
        plan=ctx.plan,
        engines=ctx.engines,
        housekeeping=ctx.housekeeping,
        bus=EventBus(),
        sync_light=hardware.sync_light,
        interrupted=threading.Event(),
    )

    assert runner.start_observe("door") is True
    assert runner.is_observing() is True
    assert hardware.light_state() == "on"

    deadline = time.monotonic() + 30
    while runner.is_observing() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert hardware.light_state() == "off"
    assert runner.snapshot()["lastObservationId"] is not None
    ctx.db.close()


def test_run_scan_waits_for_light_before_opening_stream(workdir):
    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    runtime.plan.warmup_seconds = 0.2
    started = time.monotonic()
    obs_id, status = run_scan(runtime, "door")
    assert status == "done"
    assert time.monotonic() - started >= 0.2
    runtime.db.close()


def test_run_scan_voids_when_door_opens_during_warmup(workdir):
    runtime = make_runtime(workdir, [image_layer("layer1", workdir.good)])
    runtime.plan.warmup_seconds = 5
    started = time.monotonic()
    obs_id, status = run_scan(
        runtime, "door", cancel=lambda: time.monotonic() - started > 0.1
    )
    assert status == "void"
    assert time.monotonic() - started < 2
    loaded = observations.load_observation(runtime.db, obs_id)
    assert "门被打开" in loaded["void_reason"]
    runtime.db.close()


def test_timer_respects_interval(tmp_path):
    db = _db(tmp_path)
    scheduler = Scheduler(
        config={"trig.timer_enabled": True, "trig.timer_minutes": 60},
        db=db,
        observe=SimpleNamespace(start_observe=lambda trigger: True, is_observing=lambda: False),
        hardware=_hardware(None),
        interrupted=threading.Event(),
    )
    assert scheduler._timer_due() is True

    observations.save_observation(
        db,
        status="done",
        trigger="manual",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    assert scheduler._timer_due() is False
    db.close()
