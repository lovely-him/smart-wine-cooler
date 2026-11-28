"""整柜观察：逐层串行，全层成功才算成立。

任一层失败整次作废——存下来的部分结果既不是"之前"也不是"之后"，只会污染历史。
作废的观察同样落库，否则排查"为什么今天一直不灵"时无从下手。
"""

from datetime import datetime
from pathlib import Path
import time

from server.platform.db import checkpoints, observations
from server.vision import factory
from server.observe.layer import observe

WARMUP_SLICE = 0.2


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _save_layer(image_dir, obs_id, layer_id, outcome):
    """把整层画面与逐件裁剪图落盘，返回 (整层图相对路径, [裁剪图相对路径])。"""
    target = Path(image_dir) / str(obs_id)
    target.mkdir(parents=True, exist_ok=True)

    frame_path = ""
    if outcome.frame is not None:
        file = target / f"{layer_id}.jpg"
        outcome.frame.save(file, "JPEG", quality=90)
        frame_path = str(file.relative_to(image_dir))

    crops = []
    for index, entity in enumerate(outcome.entities):
        if entity.patch is None:
            crops.append("")
            continue
        file = target / f"{layer_id}-{index}.jpg"
        entity.patch.save(file, "JPEG", quality=90)
        crops.append(str(file.relative_to(image_dir)))
    return frame_path, crops


def _tally(layers):
    """[{layer_id, entities}] → 每层各类别件数（None 表示未识别）。"""
    result = []
    for layer in layers:
        counts = {}
        for entity in layer["entities"]:
            key = entity["category_id"]
            counts[key] = counts.get(key, 0) + 1
        result.append(
            {
                "layer_id": layer["layer_id"],
                "counts": [{"category_id": k, "count": v} for k, v in counts.items()],
            }
        )
    return result


def _counts(layer):
    """核对状态里存的是驼峰 entities，观察统计是 counts，两种形状都要能算。"""
    if "counts" in layer:
        return layer["counts"]
    tally = {}
    for entity in layer.get("entities") or []:
        key = entity.get("categoryId")
        tally[key] = tally.get(key, 0) + 1
    return [{"category_id": key, "count": count} for key, count in tally.items()]


def _flatten(layers):
    flat = {}
    for layer in layers:
        layer_id = layer.get("layer_id") or layer.get("layerId")
        for item in _counts(layer):
            flat[(layer_id, item["category_id"])] = item["count"]
    return flat


def deviation(submitted, reported):
    """核对状态与某次观察的偏差：正的表示多了，负的表示少了。"""
    left = _flatten(submitted)
    right = _flatten(reported)
    items = []
    for key in set(left) | set(right):
        delta = right.get(key, 0) - left.get(key, 0)
        if delta:
            items.append({"layer_id": key[0], "category_id": key[1], "delta": delta})
    return items


def _refuse_full_disk(db, trigger, started):
    """存储满就不拍。这次失败本身也可能写不进去，写不进也不抛。"""
    try:
        return observations.save_observation(
            db,
            status="void",
            trigger=trigger,
            started_at=started,
            finished_at=now(),
            void_reason="存储已满：清掉旧记录也不够，先腾地方再来",
        )
    except Exception:
        return None


def _wait_light(plan, cancel):
    """开流前等柜内光照稳定。

    切片睡是为了能中途醒来看门：门一开就代表柜内又被人改动，等完了拍到的也不是关门时的样子。
    返回是否等满。
    """
    deadline = time.monotonic() + plan.warmup_seconds
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        if cancel is not None and cancel():
            return False
        time.sleep(min(left, WARMUP_SLICE))


def run_scan(db, image_dir, plan, engines, housekeeping, trigger, cancel=None):
    """跑一次整柜观察。返回 (observation_id, status)。"""
    started = now()
    if housekeeping.storage_low():
        housekeeping.reclaim_space()
    if housekeeping.storage_low():
        return _refuse_full_disk(db, trigger, started), "void"
    if not _wait_light(plan, cancel):
        obs_id = observations.save_observation(
            db,
            status="void",
            trigger=trigger,
            started_at=started,
            finished_at=now(),
            void_reason="门被打开，这次不算",
        )
        return obs_id, "void"
    outcomes = []
    failure = None

    for layer in plan.layers:
        if cancel is not None and cancel():
            failure = (layer, None, "门被打开，这次不算")
            break
        try:
            camera = factory.load_camera(plan.camera_of(layer), plan.grab_cfg)
        except Exception as exc:
            failure = (layer, None, f"打不开采集源: {exc}")
            break
        outcome = observe(
            camera, engines.detector, engines.classifier, plan.stability_cfg(layer)
        )
        if outcome.status != "ok":
            failure = (layer, outcome, "；".join(outcome.reasons))
            break
        outcomes.append((layer, outcome))

    if failure is None and cancel is not None and cancel():
        outcomes = []
        failure = (plan.layers[-1], None, "门被打开，这次不算")

    if failure is not None:
        layer, outcome, reason = failure
        obs_id = observations.save_observation(
            db,
            status="void",
            trigger=trigger,
            started_at=started,
            finished_at=now(),
            void_reason=f"{layer.name}：{reason}",
        )
        if outcome is not None and outcome.frame is not None:
            frame_path, _ = _save_layer(image_dir, obs_id, layer.id, outcome)
            observations.save_layer_record(
                db, obs_id, layer.id, layer.name, usable=False, reason=reason,
                image_path=frame_path, entities=[],
            )
        return obs_id, "void"

    obs_id = observations.save_observation(
        db, status="done", trigger=trigger, started_at=started, finished_at=now()
    )

    layers = []
    for layer, outcome in outcomes:
        frame_path, crops = _save_layer(image_dir, obs_id, layer.id, outcome)
        entities = [
            {
                "id": f"{obs_id}-{layer.id}-{index}",
                "box": {
                    "x": entity.box[0],
                    "y": entity.box[1],
                    "w": entity.box[2] - entity.box[0],
                    "h": entity.box[3] - entity.box[1],
                },
                "category_id": entity.category_id,
                "crop_path": crops[index],
                "score": entity.score,
                "match_score": entity.match,
            }
            for index, entity in enumerate(outcome.entities)
        ]
        layers.append({"layer_id": layer.id, "entities": entities})
        observations.save_layer_record(
            db, obs_id, layer.id, layer.name, usable=True, reason="",
            image_path=frame_path, entities=entities,
        )

    record_change(db, obs_id, _tally(layers))
    return obs_id, "done"


def record_change(db, obs_id, reported):
    """相对上一次观察确有净变化时才记一条；内容是和最新核对状态的偏差。

    还没提交过任何核对状态时什么都不记——没有基准就谈不上"差在哪"。
    第一次就没有偏差也不记，空记录只是噪声。
    """
    checkpoint = checkpoints.latest_checkpoint(db)
    if checkpoint is None:
        return

    current = deviation(checkpoint["submitted"], reported)
    previous = observations.previous_observation(db, obs_id)
    if previous is None:
        if not current:
            return
    elif deviation(checkpoint["submitted"], _tally(previous["layers"])) == current:
        return

    checkpoints.save_change(db, obs_id, current)
