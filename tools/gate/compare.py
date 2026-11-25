"""比对两次检测结果：框数、逐个 IoU、分数差。

两个后端跑同一张图，结果应当几乎重合；差得多的位置说明解码或预处理有分支，
不是量化误差。
"""

import argparse
import json
import sys

DEF_MIN_IOU = 0.90
DEF_MAX_SCORE = 0.05
DEF_MIN_COS = 0.98


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = min(ax2, bx2) - max(ax1, bx1)
    inter_h = min(ay2, by2) - max(ay1, by1)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    inter = inter_w * inter_h
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter + 1e-9)


def match(left, right):
    """按分数从高到低贪心配对，返回 (配对, 左边多出来的, 右边多出来的)。"""
    remaining = list(range(len(right)))
    pairs = []
    used = set()
    for item in sorted(left, key=lambda d: -d["score"]):
        if not remaining:
            break
        best = max(remaining, key=lambda idx: iou(item["box"], right[idx]["box"]))
        score = iou(item["box"], right[best]["box"])
        if score <= 0:
            continue
        pairs.append((item, right[best], score))
        used.add(item["id"])
        remaining.remove(best)
    unmatched_left = [item for item in left if item["id"] not in used]
    unmatched_right = [right[idx] for idx in remaining]
    return pairs, unmatched_left, unmatched_right


def compare_vectors(left, right, min_cos):
    a = left["vector"]
    b = right["vector"]
    if len(a) != len(b):
        print(f"FAIL 维度不一致 {len(a)} vs {len(b)}")
        return 1
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    cosine = dot / (norm_a * norm_b + 1e-12)
    print(f"{left['backend']}: {len(a)} 维   {right['backend']}: {len(b)} 维")
    print(f"  余弦相似度 {cosine:.6f}")
    if cosine < min_cos:
        print(f"FAIL 余弦 {cosine:.6f} < {min_cos}")
        return 1
    print(f"PASS 余弦 {cosine:.6f}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="比对两个后端的推理结果")
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--min-iou", type=float, default=DEF_MIN_IOU)
    parser.add_argument("--max-score", type=float, default=DEF_MAX_SCORE)
    parser.add_argument("--min-cos", type=float, default=DEF_MIN_COS)
    args = parser.parse_args()

    with open(args.left, encoding="utf-8") as fp:
        left = json.load(fp)
    with open(args.right, encoding="utf-8") as fp:
        right = json.load(fp)

    if "vector" in left:
        return compare_vectors(left, right, args.min_cos)

    a = [dict(item, id=idx) for idx, item in enumerate(left["detections"])]
    b = [dict(item, id=idx) for idx, item in enumerate(right["detections"])]
    print(f"{left['backend']}: {len(a)} 个框   {right['backend']}: {len(b)} 个框")

    if len(a) != len(b):
        print("FAIL 框数不一致")
        return 1
    if not a:
        print("PASS 两边都是 0 个框")
        return 0

    pairs, only_left, only_right = match(a, b)
    if only_left or only_right:
        print(f"FAIL 有 {len(only_left) + len(only_right)} 个框没配上")
        return 1

    worst_iou = min(pair[2] for pair in pairs)
    worst_score = max(abs(pair[0]["score"] - pair[1]["score"]) for pair in pairs)
    for item, other, score in pairs:
        print(f"  IoU {score:.5f}   分数 {item['score']:.4f} / {other['score']:.4f}")

    if worst_iou < args.min_iou:
        print(f"FAIL 最小 IoU {worst_iou:.5f} < {args.min_iou}")
        return 1
    if worst_score > args.max_score:
        print(f"FAIL 最大分数差 {worst_score:.4f} > {args.max_score}")
        return 1

    print(f"PASS 最小 IoU {worst_iou:.5f}，最大分数差 {worst_score:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
