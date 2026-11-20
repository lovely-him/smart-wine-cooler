"""帧间一致性度量与置信分档。

这里**不做投票**。多帧只用来估计"这次拍得好不好"：数量、分数、框位置抖不抖，
以及单帧内部有没有可疑形态。原因见 docs/inventory-loop.md §3.4：
模型偏差在帧间并不独立，投票挑不出正确答案，反而会把错误固化。

分档只影响"怎么呈现、要不要问人"，不改变观测内容。
"""

import math

import numpy as np

# ── 路径与常量 ──
DEF_MATCH_IOU = 0.5
DEF_SCORE_MARGIN = 0.10
DEF_SCORE_STD = 0.02
DEF_JITTER = 0.15
DEF_DUP_IOU = 0.85
DEF_DUP_SHIFT = 0.05
DEF_CONTRAST_MIN = 6.0

TIER_TRUST = "可信"
TIER_DOUBT = "存疑"
TIER_INVALID = "失效"


# ── 函数 ──
def iou(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def box_contrast(img, box):
    """框内与框外邻域的平均灰度差：越小说明目标与背景越难分。"""
    width, height = img.size
    x1 = int(max(0, box[0] * width))
    y1 = int(max(0, box[1] * height))
    x2 = int(min(width, box[2] * width))
    y2 = int(min(height, box[3] * height))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 0.0
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    pad = max(2, int((x2 - x1 + y2 - y1) * 0.1))
    ox1, oy1 = max(0, x1 - pad), max(0, y1 - pad)
    ox2, oy2 = min(width, x2 + pad), min(height, y2 + pad)
    inner = gray[y1:y2, x1:x2].mean()
    outer = gray[oy1:oy2, ox1:ox2].mean()
    return float(inner - outer)


def _match_to_anchor(anchor, dets, match_iou):
    """把一帧的检测匹配到锚帧目标上，返回 {锚帧下标: (检测, iou)} 与多余检测数。"""
    assigned = {}
    used = set()
    for ai, base in enumerate(anchor):
        best, best_iou, best_di = None, 0.0, None
        for di, det in enumerate(dets):
            if di in used:
                continue
            value = iou(base["box"], det["box"])
            if value > best_iou:
                best, best_iou, best_di = det, value, di
        if best is not None and best_iou >= match_iou:
            assigned[ai] = (best, best_iou)
            used.add(best_di)
    return assigned, len(dets) - len(used)


def evaluate(frames, per_frame, infer_cfg, cfg, stable_from_frame_pipeline=True):
    """frames 与 per_frame 一一对应；锚帧取中间那一帧。"""
    match_iou = float(cfg.get("match_iou", DEF_MATCH_IOU))
    score_margin = float(cfg.get("score_margin", DEF_SCORE_MARGIN))
    score_std_max = float(cfg.get("score_std_max", DEF_SCORE_STD))
    jitter_max = float(cfg.get("jitter_max", DEF_JITTER))
    dup_iou = float(cfg.get("overlap_dup_iou", DEF_DUP_IOU))
    dup_shift = float(cfg.get("overlap_dup_shift", DEF_DUP_SHIFT))
    contrast_min = cfg.get("contrast_min")
    conf = float(infer_cfg.get("conf", 0.0))

    anchor_idx = len(per_frame) // 2
    anchor = per_frame[anchor_idx]
    counts = [len(dets) for dets in per_frame]
    n_spread = max(counts) - min(counts) if counts else 0

    matched = {ai: [] for ai in range(len(anchor))}
    unmatched = 0
    for fi, dets in enumerate(per_frame):
        assigned, extra = _match_to_anchor(anchor, dets, match_iou)
        unmatched += extra
        for ai, (det, _) in assigned.items():
            matched[ai].append((fi, det))
    for ai, hits in matched.items():
        if len(hits) < len(per_frame):
            unmatched += len(per_frame) - len(hits)

    score_std = 0.0
    jitter = 0.0
    for ai, hits in matched.items():
        if len(hits) >= 2:
            score_std = max(score_std, float(np.std([h[1]["score"] for h in hits])))
        base = anchor[ai]["box"]
        cx, cy = center(base)
        diag = math.hypot(base[2] - base[0], base[3] - base[1]) or 1.0
        for _, det in hits:
            dx, dy = center(det["box"])
            jitter = max(jitter, math.hypot(dx - cx, dy - cy) / diag)

    dup = 0
    for i in range(len(anchor)):
        for j in range(i + 1, len(anchor)):
            if iou(anchor[i]["box"], anchor[j]["box"]) >= dup_iou:
                (ax, ay) = center(anchor[i]["box"])
                (bx, by) = center(anchor[j]["box"])
                if math.hypot(ax - bx, ay - by) > dup_shift:
                    dup += 1

    contrasts = [box_contrast(frames[anchor_idx], det["box"]) for det in anchor]
    weak_contrast = 0
    if contrast_min is not None:
        weak_contrast = sum(1 for value in contrasts if value < float(contrast_min))
    near_threshold = sum(1 for det in anchor if det["score"] - conf < score_margin)

    reasons = []
    if not stable_from_frame_pipeline:
        reasons.append("画面未稳定")
    if n_spread:
        reasons.append(f"数量不一致（极差 {n_spread}）")
    if unmatched:
        reasons.append(f"闪现目标 {unmatched}")
    if score_std > score_std_max:
        reasons.append(f"分数抖动 {score_std:.3f}")
    if jitter > jitter_max:
        reasons.append(f"框抖动 {jitter:.3f}")
    if dup:
        reasons.append(f"重复框 {dup}")
    if near_threshold:
        reasons.append(f"贴阈值目标 {near_threshold}")
    if weak_contrast:
        reasons.append(f"框内外对比弱 {weak_contrast}")

    if not stable_from_frame_pipeline:
        tier = TIER_INVALID
    elif reasons:
        tier = TIER_DOUBT
    else:
        tier = TIER_TRUST

    return {
        "tier": tier,
        "reasons": reasons,
        "anchor_index": anchor_idx,
        "counts": counts,
        "metrics": {
            "n_spread": n_spread,
            "unmatched": unmatched,
            "score_std": round(score_std, 4),
            "jitter": round(jitter, 4),
            "overlap_dup": dup,
            "weak_contrast": weak_contrast,
            "near_threshold": near_threshold,
            "contrast": [round(v, 2) for v in contrasts],
        },
        "detections": [
            {"cls": det["cls"], "score": det["score"], "box": det["box"]} for det in anchor
        ],
    }
