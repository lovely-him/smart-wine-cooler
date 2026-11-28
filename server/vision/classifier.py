"""分类：裁剪图 → 融合特征 → 多原型检索 → 类别或「未识别」。

认不出就返回 None，由上层记成未识别。这是刻意保留的保守行为：宁可问人，也不猜。
"""

import numpy as np

from .features import color_hist, crop, fuse

DEF_W_EMBED = 0.5
DEF_TOP_K = 1
DEF_MARGIN = 0.02
DEF_PAD = 0.06


class ReferenceIndex:
    """按类别 id 组织（名字可改、可合并，id 才稳定）。同类多张原型存成矩阵。"""

    def __init__(self):
        self.groups = {}
        self.names = {}

    def __len__(self):
        return sum(len(m) for m in self.groups.values())

    def add(self, category_id, name, vector):
        vec = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if category_id in self.groups:
            self.groups[category_id] = np.vstack([self.groups[category_id], vec])
        else:
            self.groups[category_id] = vec
        self.names[category_id] = name

    def name_of(self, category_id):
        return self.names.get(category_id, f"#{category_id}")

    def max_similarity(self, category_id, vector):
        matrix = self.groups.get(category_id)
        if matrix is None or len(matrix) == 0:
            return 0.0
        return float(np.max(matrix @ np.asarray(vector, dtype=np.float32).reshape(-1)))

    def should_add(self, category_id, vector, dedupe, cap):
        """同类里已有高度相似的样本就不重复入库；超过配额也拒绝。"""
        if cap and len(self.groups.get(category_id, ())) >= int(cap):
            return False, "已达该类配额"
        if dedupe and self.max_similarity(category_id, vector) >= float(dedupe):
            return False, "与已有参考图重复"
        return True, ""

    @classmethod
    def from_rows(cls, rows):
        index = cls()
        for row in rows:
            index.add(row["category_id"], row["name"], row["vector"])
        return index


def scores_of(query, groups, top_k):
    """每个类取该类的 N 张原型里最相似的 top_k 张之均值。"""
    scores = {}
    for cid, matrix in groups.items():
        sims = matrix @ query
        k = max(1, min(int(top_k), sims.shape[0]))
        scores[cid] = float(np.sort(sims)[-k:].mean())
    return scores


def decide(scores, tau, tau_single, margin):
    """返回 (类别 id | None, 详情)。

    主判据是「第一名比第二名领先多少」而不是绝对相似度：实测类内最小相似度低于
    类间最大相似度，任何单阈值都会同时错杀与漏放。
    """
    if not scores:
        return None, {"reason": "参考库为空"}

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    detail = {
        "best_score": round(best_score, 4),
        "second": ranked[1][0] if len(ranked) > 1 else None,
        "margin": round(best_score - (ranked[1][1] if len(ranked) > 1 else 0.0), 4),
    }

    if len(ranked) == 1:
        if best_score < max(tau, tau_single):
            return None, {**detail, "reason": "相似度低于下限"}
        return best, {**detail, "reason": "ok"}

    if best_score < tau:
        return None, {**detail, "reason": "相似度低于下限"}
    if best_score - ranked[1][1] < margin:
        return None, {**detail, "reason": "与第二名区分不够"}
    return best, {**detail, "reason": "ok"}


class Classifier:
    def __init__(self, cfg, index, embedder):
        self.index = index
        self.embedder = embedder
        self.w_embed = float(cfg.get("w_embed", DEF_W_EMBED))
        self.top_k = int(cfg.get("top_k", DEF_TOP_K))
        self.tau = float(cfg["tau"])
        self.tau_single = float(cfg.get("tau_single", cfg["tau"]))
        self.margin = float(cfg.get("margin", DEF_MARGIN))
        self.pad = float(cfg.get("pad", DEF_PAD))

    def feature(self, img):
        return fuse(self.embedder.embed(img), color_hist(img), self.w_embed)

    def classify(self, img):
        vec = self.feature(img)
        category_id, detail = decide(
            scores_of(vec, self.index.groups, self.top_k),
            self.tau,
            self.tau_single,
            self.margin,
        )
        return {
            "category_id": category_id,
            "category": self.index.name_of(category_id) if category_id else None,
            **detail,
        }
