"""比对两份 detect_json.py 产物，给出精度门禁结论。

硬门槛（决定通过/不通过）：
  1. 每张图检测数量一致
  2. 类别集合一致（当前只检 bottle，等价于数量一致）

软指标（只记录，不判失败）：
  3. 按中心点顺序配对后的 IoU 最小值/均值
  4. 分数差的最大值与均值
  5. 两侧推理耗时

用法:
    python compare.py --ref host.json --test board.json [--iou-min 0.7] [--strict]
"""

import argparse
import json
import sys

# ── 路径与常量 ──


# ── 函数 ──
def load(path):
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    return {item["file"]: item for item in data["images"]}, data


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True, help="基线结果（通常是宿主 onnx）")
    parser.add_argument("--test", required=True, help="待验证结果（通常是板端 rknn）")
    parser.add_argument("--iou-min", type=float, default=0.7, help="软指标提示线")
    parser.add_argument("--strict", action="store_true", help="软指标也纳入判定")
    args = parser.parse_args()

    ref, ref_meta = load(args.ref)
    test, test_meta = load(args.test)

    print(f"基线 {ref_meta['backend']:5s} {ref_meta['model']}")
    print(f"待验 {test_meta['backend']:5s} {test_meta['model']}\n")

    only_ref = sorted(set(ref) - set(test))
    only_test = sorted(set(test) - set(ref))
    if only_ref or only_test:
        print(f"[FAIL] 图片集合不一致: 仅基线 {only_ref} / 仅待验 {only_test}")
        return 1

    print(f"{'文件':24s} {'基线':>4s} {'待验':>4s} {'配对数':>6s} {'IoU最小':>8s} {'分数差':>7s}")
    count_fail = []
    ious, diffs = [], []

    for name in sorted(ref):
        left = ref[name]["detections"]
        right = test[name]["detections"]
        if len(left) != len(right):
            count_fail.append((name, len(left), len(right)))
            print(f"{name[-24:]:24s} {len(left):4d} {len(right):4d}  数量不一致")
            continue

        pairs = []
        for det in left:
            best = max(range(len(right)), key=lambda i: iou(det["box"], right[i]["box"]), default=None)
            if best is not None and best not in [p[0] for p in pairs]:
                pairs.append((best, det))

        local_iou = [iou(det["box"], right[i]["box"]) for i, det in pairs]
        local_diff = [abs(det["score"] - right[i]["score"]) for i, det in pairs]
        ious.extend(local_iou)
        diffs.extend(local_diff)

        lo = f"{min(local_iou):.3f}" if local_iou else "-"
        df = f"{max(local_diff):.3f}" if local_diff else "-"
        print(f"{name[-24:]:24s} {len(left):4d} {len(right):4d} {len(pairs):6d} {lo:>8s} {df:>7s}")

    ref_ms = sum(i["infer_ms"] for i in ref_meta["images"]) / len(ref_meta["images"])
    test_ms = sum(i["infer_ms"] for i in test_meta["images"]) / len(test_meta["images"])
    print(f"\n推理耗时: 基线 {ref_ms:.1f}ms / 待验 {test_ms:.1f}ms")

    if ious:
        print(f"IoU: 最小 {min(ious):.3f} 均值 {sum(ious) / len(ious):.3f}")
    if diffs:
        print(f"分数差: 最大 {max(diffs):.3f} 均值 {sum(diffs) / len(diffs):.3f}")

    soft_fail = []
    if ious and min(ious) < args.iou_min:
        soft_fail.append(f"IoU 最小值 {min(ious):.3f} < {args.iou_min}")

    if count_fail:
        print(f"\n[FAIL] 数量不一致 {len(count_fail)} 张: {[c[0][-12:] for c in count_fail]}")
        return 1
    if args.strict and soft_fail:
        print(f"\n[FAIL] 软指标未达标: {'; '.join(soft_fail)}")
        return 1

    print("\n[ OK ] 硬门槛通过：每图数量一致" + (f"（软指标提示: {'; '.join(soft_fail)}）" if soft_fail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
