"""把库里取出来的行转成界面要的形状。

图片地址是 HTTP 里的路径（`/img/...`），库里存的是相对图片目录的路径；
两者的换算只在这里做一次，别处不再各写一套。
"""

IMAGE_ROOT = "/img"
REFERENCE_ROOT = "/ref"


def rel(path):
    if path and path.startswith(f"{IMAGE_ROOT}/"):
        return path[len(IMAGE_ROOT) + 1 :]
    return path or ""


def _exists(image_dir, path):
    return bool(path) and (image_dir / path).is_file()


def as_entity(image_dir, row):
    crop = row["crop_url"]
    return {
        "id": row["id"],
        "box": row["box"],
        "categoryId": row["category_id"],
        "cropUrl": f"{IMAGE_ROOT}/{crop}" if crop else None,
        "score": row["score"],
    }


def as_layer(image_dir, layer):
    path = layer["image_url"]
    if not _exists(image_dir, path):
        path = ""
    return {
        "layerId": layer["layer_id"],
        "layerName": layer.get("layer_name") or "",
        "imageUrl": f"{IMAGE_ROOT}/{path}" if path else None,
        "entities": [as_entity(image_dir, row) for row in layer["entities"]],
    }


def as_observation(image_dir, observation):
    if observation is None:
        return None
    return {
        "id": observation["id"],
        "status": observation["status"],
        "voidReason": observation["void_reason"] or None,
        "at": observation["finished_at"] or observation["started_at"],
        "layers": [as_layer(image_dir, layer) for layer in observation["layers"]],
    }


def _as_stored_entity(image_dir, entity):
    crop = rel(entity.get("cropUrl"))
    if not _exists(image_dir, crop):
        crop = ""
    return {**entity, "cropUrl": f"{IMAGE_ROOT}/{crop}" if crop else None}


def _as_stored_layer(image_dir, layer):
    path = rel(layer.get("imageUrl"))
    if not _exists(image_dir, path):
        path = ""
    return {
        "layerId": layer["layerId"],
        "imageUrl": f"{IMAGE_ROOT}/{path}" if path else None,
        "entities": [_as_stored_entity(image_dir, e) for e in layer["entities"]],
    }


def as_checkpoint(image_dir, checkpoint):
    if checkpoint is None:
        return None
    return {
        "id": checkpoint["id"],
        "basedOnObservation": checkpoint["based_on_observation"],
        "at": checkpoint["created_at"],
        "layers": [_as_stored_layer(image_dir, layer) for layer in checkpoint["submitted"]],
        "note": checkpoint["note"],
    }


def as_change(change):
    return {
        "id": change["id"],
        "observationId": change["observation_id"],
        "at": change["at"],
        "items": change["items"],
    }


def as_category(category):
    return {
        "id": category["id"],
        "name": category["name"],
        "shape": category["shape"] or "bordeaux",
        "details": category["details"],
        "refs": [
            {"id": str(ref["id"]), "url": f"{REFERENCE_ROOT}/{ref['path']}"}
            for ref in category["refs"]
        ],
    }
