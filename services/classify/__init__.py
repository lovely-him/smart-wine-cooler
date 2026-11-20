"""分类器：裁剪图 → 融合特征 → 多原型检索 → 类别或"未知"。

判定不出类别时返回 None，由业务流程记成「未识别」并最终交给用户确认——
这是设计上刻意保留的保守行为：宁可问人，也不猜。
"""

import numpy as np

from services.classify import retrieval
from services.classify.color import color_hist
from services.classify.features import crop_normalized, fuse
from services.classify.reference import ReferenceIndex

# ── 路径与常量 ──
DEF_PAD = 0.06


# ── 类 ──
class Classifier:
    def __init__(self, cfg, store, embedder):
        self.store = store
        self.embedder = embedder
        self.w_embed = float(cfg.get("w_embed", 0.5))
        self.top_k = int(cfg.get("top_k", retrieval.DEF_TOP_K))
        self.tau = float(cfg.get("tau", retrieval.DEF_TAU))
        self.tau_single = float(cfg.get("tau_single", retrieval.DEF_TAU))
        self.margin = float(cfg.get("margin", retrieval.DEF_MARGIN))
        self.dedupe = float(cfg.get("dedupe", 0.95))
        self.cap = int(cfg.get("per_category_cap", 0))
        self.pad = float(cfg.get("pad", DEF_PAD))
        self.index = ReferenceIndex.from_store(store) if store is not None else ReferenceIndex()

    def reload(self):
        if self.store is not None:
            self.index = ReferenceIndex.from_store(self.store)
        return self.index

    def feature(self, img):
        return fuse(self.embedder.embed(img), color_hist(img), self.w_embed)

    def classify(self, img):
        """img 是裁好的裁剪图。返回 {category_id, category, reason, ...}，id 为 None 表示未知。"""
        vec = self.feature(img)
        scores = retrieval.category_scores(vec, self.index.groups, self.top_k)
        category_id, detail = retrieval.decide(scores, self.tau, self.margin, self.tau_single)
        return {
            "category_id": category_id,
            "category": self.index.name_of(category_id) if category_id else None,
            "feature": vec,
            **detail,
        }

    def known(self):
        return self.index.categories()

    def add_reference(self, category_id, vector, path, source, quality=0.0):
        """入库（带去重与配额）；返回 (是否入库, 原因)。

        索引里的名字必须从库里现查：新建类别时索引还没有它，否则会存成 `#id` 这种占位名，
        之后所有分类结果都显示成 `#1`。
        """
        ok, why = self.index.should_add(category_id, vector, self.dedupe, self.cap)
        if not ok:
            return False, why
        if self.store is not None:
            self.store.add_reference_by_id(
                category_id, np.asarray(vector, dtype=np.float32), path, source, quality
            )
        name = self.index.names.get(category_id)
        if not name and self.store is not None:
            name = self.store.category_names().get(category_id, f"#{category_id}")
        self.index.add(category_id, name, vector)
        return True, ""


# ── 函数 ──
def load_classifier(cfg, store, embedder):
    if not cfg or not cfg.get("enabled", True):
        return None
    return Classifier(cfg, store, embedder)
