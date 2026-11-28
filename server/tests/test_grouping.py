"""零档案辅助的分组：只把「看起来一样」的归到一起，不像的不硬凑。"""

import numpy as np

from server.catalog.grouping import cluster, unknown_groups


def unit(*values):
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_similar_items_land_in_one_group():
    vectors = [
        ("a", unit(1.0, 0.0)),
        ("b", unit(0.99, 0.14)),
        ("c", unit(0.0, 1.0)),
    ]

    assert cluster(vectors, 0.8) == [["a", "b"], ["c"]]


def test_unlike_items_stay_apart():
    vectors = [("a", unit(1.0, 0.0)), ("b", unit(0.0, 1.0)), ("c", unit(-1.0, 0.0))]

    assert cluster(vectors, 0.8) == [["a"], ["b"], ["c"]]


def test_representative_stays_first_member():
    """代表固定为第一件：否则分组结果会随顺序漂移。"""
    vectors = [
        ("a", unit(1.0, 0.0)),
        ("b", unit(0.9, 0.44)),
        ("c", unit(0.5, 0.87)),
    ]

    assert cluster(vectors, 0.8) == [["a", "b"], ["c"]]


def test_threshold_is_inclusive():
    vectors = [("a", unit(1.0, 0.0)), ("b", unit(0.8, 0.6))]

    assert cluster(vectors, 0.8) == [["a", "b"]]


def _runtime(tmp_path, mapping):
    """把裁剪图路径映射成固定向量，省掉真跑模型。"""
    from types import SimpleNamespace

    from PIL import Image

    images = tmp_path / "images"
    (images / "1").mkdir(parents=True)

    class Stub:
        def feature(self, image):
            return mapping[image.getpixel((0, 0))[0]]

    for name, color in (("a.jpg", 0), ("b.jpg", 1), ("c.jpg", 2)):
        Image.new("RGB", (8, 8), (color, 0, 0)).save(images / "1" / name)

    return SimpleNamespace(image_dir=images, engines=SimpleNamespace(classifier=Stub()))


def _observation(*pairs):
    return {
        "layers": [
            {
                "layer_id": "layer1",
                "entities": [
                    {"id": f"1-layer1-{index}", "category_id": category, "crop_url": crop}
                    for index, (category, crop) in enumerate(pairs)
                ],
            }
        ]
    }


def test_unknown_groups_reads_crops_and_skips_recognized(tmp_path):
    same, other = unit(1.0, 0.0), unit(0.0, 1.0)
    runtime = _runtime(tmp_path, {0: same, 1: same, 2: other})
    observation = _observation((None, "1/a.jpg"), (7, "1/b.jpg"), (None, "1/c.jpg"))

    groups = unknown_groups(
        image_dir=runtime.image_dir, engines=runtime.engines, observation=observation, threshold=0.8
    )

    assert groups == []


def test_unknown_groups_pairs_lookalikes(tmp_path):
    same = unit(1.0, 0.0)
    runtime = _runtime(tmp_path, {0: same, 1: same, 2: unit(0.0, 1.0)})
    observation = _observation((None, "1/a.jpg"), (None, "1/b.jpg"), (None, "1/c.jpg"))

    groups = unknown_groups(
        image_dir=runtime.image_dir, engines=runtime.engines, observation=observation, threshold=0.8
    )

    assert groups == [{"layerId": "layer1", "entityIds": ["1-layer1-0", "1-layer1-1"]}]
