"""提交：把编辑草稿落成新的核对状态，并把确认过的裁剪图收进参考库。

提交是**整柜**的，不能只提交某一层——核对状态本身就是整柜事实。
没提交过的部分保持原样，所以基准观察里的一层如果不在提交里，就不写进新版本。
"""

from datetime import datetime
from pathlib import Path

from PIL import Image

from server.kernel import ordering
from server.platform.db import checkpoints, observations, references


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ConflictError(Exception):
    """起点观察已被更靠后的成立观察取代，人得重新决定。"""

    def __init__(self, latest_observation_id):
        super().__init__("期间来过新的观察")
        self.latest_observation_id = latest_observation_id


def _layers_of(observation):
    return [
        {
            "layerId": layer["layer_id"],
            "imageUrl": layer["image_url"] or None,
            "entities": [
                {
                    "id": entity["id"],
                    "box": entity["box"],
                    "categoryId": entity["category_id"],
                    "cropUrl": entity["crop_url"],
                }
                for entity in layer["entities"]
            ],
        }
        for layer in observation["layers"]
    ]


def _entities_of(observation):
    return {
        layer["layer_id"]: {
            "image_url": layer["image_url"],
            "entities": {entity["id"]: entity for entity in layer["entities"]},
        }
        for layer in observation["layers"]
    }


def _crop_new_entity(image_dir, image_url, box, name):
    """人工新增的那件没有现成裁剪图，按框从整层画面裁一张存下来。"""
    if not image_url:
        return ""
    source = image_dir / image_url
    if not source.is_file():
        return ""

    target_dir = image_dir / Path(image_url).parent
    target = target_dir / f"{name}.jpg"
    with Image.open(source) as frame:
        width, height = frame.size
        left = int(max(0, box.x * width))
        top = int(max(0, box.y * height))
        right = int(min(width, (box.x + box.w) * width))
        bottom = int(min(height, (box.y + box.h) * height))
        if right - left < 2 or bottom - top < 2:
            return ""
        frame.convert("RGB").crop((left, top, right, bottom)).save(target, "JPEG", quality=90)
    return str(target.relative_to(image_dir))


def _store_reference(db, image_dir, reference_dir, engines, config, category_id, crop_url):
    """把确认过的裁剪图拷进参考库并落特征；重复的、超额的不再收。"""
    if not crop_url:
        return
    source = image_dir / crop_url
    if not source.is_file():
        return

    with Image.open(source) as image:
        patch = image.convert("RGB")
        vector = engines.classifier.feature(patch)

        ok, why = engines.classifier.index.should_add(
            category_id,
            vector,
            float(config.get("judge.ref_dedupe") or 0),
            int(config.get("judge.ref_cap") or 0),
        )
        if not ok:
            return

        target_dir = reference_dir / str(category_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{now().replace(':', '')}-{len(list(target_dir.iterdir()))}.jpg"
        patch.save(target, "JPEG", quality=90)

    references.add_reference(
        db,
        category_id,
        str(target.relative_to(reference_dir)),
        vector,
        vector.shape[0],
    )
    name = engines.classifier.index.name_of(category_id)
    engines.classifier.index.add(category_id, name, vector)


def commit(db, image_dir, reference_dir, config, engines, body):
    observation = observations.load_observation(db, body.base_observation_id)
    if observation is None:
        raise ValueError("找不到作为起点的观察")

    latest = observations.latest_done_observation(db)
    if not body.force and latest is not None and latest["id"] > observation["id"]:
        raise ConflictError(latest["id"])

    source = _entities_of(observation)
    submitted = []
    for layer in body.layers:
        known = source.get(layer.layer_id, {"image_url": None, "entities": {}})
        entities = []
        for index, item in enumerate(
            sorted(layer.entities, key=lambda entry: ordering.key_xywh(entry.box.model_dump()))
        ):
            existing = known["entities"].get(item.id) if item.id else None
            crop_url = (existing or {}).get("crop_url") or ""
            entity_id = item.id or f"{observation['id']}-{layer.layer_id}-add{index}"
            if not crop_url:
                crop_url = _crop_new_entity(
                    image_dir, known["image_url"], item.box, f"{layer.layer_id}-{entity_id}"
                )
            entities.append(
                {
                    "id": entity_id,
                    "box": item.box.model_dump(),
                    "categoryId": item.category_id,
                    "cropUrl": crop_url or None,
                }
            )
        submitted.append(
            {"layerId": layer.layer_id, "imageUrl": known["image_url"], "entities": entities}
        )

    reported = _layers_of(observation)

    checkpoint_id = checkpoints.save_checkpoint(
        db,
        based_on_observation=observation["id"],
        reported=reported,
        submitted=submitted,
        note=body.note,
    )

    for layer in submitted:
        for entity in layer["entities"]:
            if entity["categoryId"] is not None:
                _store_reference(
                    db, image_dir, reference_dir, engines, config,
                    entity["categoryId"], entity["cropUrl"],
                )

    engines.reload_references()
    return checkpoint_id
