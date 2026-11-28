"""零档案辅助：把「看起来一样」的未识别件分到一组。

只做提示，不做自动命名——分组错了人可以直接拆开。
分组结果不落库：它是"这一刻怎么看"的临时判断，不是事实。
"""

from pathlib import Path

import numpy as np
from PIL import Image


def cluster(vectors, threshold):
    """贪婪聚类：与已有组的代表够像就归进去，否则自成一组的代表。

    代表固定为组里第一件，不随成员更新——否则阈值的行为会随扫描顺序漂移。
    """
    groups = []
    for entity_id, vector in vectors:
        for group in groups:
            if float(group["vector"] @ vector) >= threshold:
                group["ids"].append(entity_id)
                break
        else:
            groups.append({"vector": vector, "ids": [entity_id]})
    return [group["ids"] for group in groups]


def unknown_groups(image_dir, engines, observation, threshold):
    """按层把未识别的件聚类，只返回真的成得了组的（两件以上）。"""
    if engines.classifier is None:
        return []

    result = []
    for layer in observation["layers"]:
        vectors = []
        for entity in layer["entities"]:
            if entity["category_id"] is not None or not entity["crop_url"]:
                continue
            path = Path(image_dir) / entity["crop_url"]
            if not path.is_file():
                continue
            with Image.open(path) as image:
                vector = np.asarray(engines.classifier.feature(image.convert("RGB")), np.float32)
            vectors.append((entity["id"], vector))
        for ids in cluster(vectors, threshold):
            if len(ids) >= 2:
                result.append({"layerId": layer["layer_id"], "entityIds": ids})
    return result
