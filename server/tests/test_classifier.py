"""分类判定：认不出要干净地返回 None，不能因为缺字段把上层拖崩。

用替身 embedder，不碰 NPU，纯算法验证。
"""

import numpy as np
import pytest

from server.vision.classifier import Classifier, ReferenceIndex, decide, scores_of


class StubEmbedder:
    def __init__(self, vector):
        self.vector = np.asarray(vector, dtype=np.float32)

    def embed(self, img):
        return self.vector


class StubImage:
    """最小替身：只提供 convert/resize/size，够 color_hist 用。"""

    size = (16, 16)

    def convert(self, mode):
        return self

    def resize(self, size, resample=None):
        return self

    def __array__(self, dtype=None):
        return np.zeros((16, 16, 3), dtype=np.float32)


def index_with(mapping):
    index = ReferenceIndex()
    for category_id, (name, vector) in mapping.items():
        index.add(category_id, name, vector)
    return index


def test_empty_library_reports_reason():
    category_id, detail = decide({}, tau=0.68, tau_single=0.68, margin=0.02)
    assert category_id is None
    assert detail["reason"] == "参考库为空"


def test_classify_with_empty_library_does_not_crash():
    classifier = Classifier({"tau": 0.68, "margin": 0.02}, ReferenceIndex(), StubEmbedder([1.0, 0.0]))
    result = classifier.classify(StubImage())
    assert result["category_id"] is None
    assert result["reason"] == "参考库为空"


def test_scores_take_best_prototype():
    query = np.array([1.0, 0.0], dtype=np.float32)
    groups = {1: np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)}
    assert scores_of(query, groups, top_k=1)[1] == pytest.approx(1.0)


def test_single_category_below_tau_is_unknown():
    category_id, detail = decide({1: 0.5}, tau=0.68, tau_single=0.68, margin=0.02)
    assert category_id is None
    assert detail["reason"] == "相似度低于下限"


def test_small_margin_is_unknown():
    category_id, detail = decide({1: 0.90, 2: 0.89}, tau=0.68, tau_single=0.68, margin=0.02)
    assert category_id is None
    assert detail["reason"] == "与第二名区分不够"


def test_clear_winner_is_accepted():
    category_id, detail = decide({1: 0.90, 2: 0.60}, tau=0.68, tau_single=0.68, margin=0.02)
    assert category_id == 1
    assert detail["reason"] == "ok"
    assert detail["margin"] == pytest.approx(0.30)


def test_classify_returns_known_category():
    embedder = StubEmbedder(np.array([1.0, 0.0], dtype=np.float32))
    classifier = Classifier({"tau": 0.68, "margin": 0.02}, ReferenceIndex(), embedder)

    probe = StubImage()
    classifier.index.add(7, "示例", classifier.feature(probe))

    result = classifier.classify(probe)
    assert result["category_id"] == 7
    assert result["category"] == "示例"
    assert result["reason"] == "ok"
