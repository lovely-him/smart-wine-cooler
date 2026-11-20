"""检索与拒识：每类多原型，取最相似的一张打分；靠 τ 下限 + 类间 margin 决定认不认。

为什么不能只用绝对阈值：实测类内最小相似度低于类间最大相似度（重叠），
任何单一阈值都会同时错杀与漏放。所以主判据是"第一名比第二名领先多少"。
"""

import numpy as np

# ── 路径与常量 ──
DEF_TOP_K = 1
DEF_TAU = 0.0
DEF_MARGIN = 0.0

REASON_OK = "ok"
REASON_EMPTY = "参考库为空"
REASON_LOW = "相似度低于下限"
REASON_TIGHT = "与第二名区分不够"
REASON_UNCALIBRATED = "阈值未标定"


# ── 函数 ──
def category_scores(query, groups, top_k=DEF_TOP_K):
    """groups: {类别: ndarray(N, D)}；返回 {类别: 该类前 top_k 张的均值}。"""
    scores = {}
    for name, matrix in groups.items():
        if matrix is None or len(matrix) == 0:
            continue
        sims = matrix @ query
        k = max(1, min(int(top_k), sims.shape[0]))
        scores[name] = float(np.sort(sims)[-k:].mean())
    return scores


def decide(scores, tau=DEF_TAU, margin=DEF_MARGIN, tau_single=DEF_TAU):
    """返回 (类别 | None, 详情)。类别为 None 表示"不认识"，交回给用户。"""
    if not scores:
        return None, {"reason": REASON_EMPTY}
    if not tau:
        return None, {"reason": REASON_UNCALIBRATED}

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    second, second_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))

    detail = {
        "best": best,
        "best_score": round(best_score, 4),
        "second": second,
        "second_score": round(second_score, 4),
        "margin": round(best_score - second_score, 4),
    }

    if len(ranked) == 1:
        limit = max(tau, tau_single)
        if best_score < limit:
            return None, {**detail, "reason": REASON_LOW}
        return best, {**detail, "reason": REASON_OK}

    if best_score < tau:
        return None, {**detail, "reason": REASON_LOW}
    if best_score - second_score < margin:
        return None, {**detail, "reason": REASON_TIGHT}
    return best, {**detail, "reason": REASON_OK}
