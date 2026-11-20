"""比对两份特征门禁产物：逐图余弦相似度。

特征没有"框"可比，所以判据只有一条——同一张图在两个运行时上的特征必须几乎相同。
低于阈值说明量化或预处理出了问题，不能拿去做检索。
"""

import argparse
import json
import math
import sys

# ── 路径与常量 ──
DEF_COS_MIN = 0.99


# ── 函数 ──
def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True, help="基线特征（通常是宿主 onnx）")
    parser.add_argument("--test", required=True, help="待验特征（通常是板卡 rknn）")
    parser.add_argument("--cos-min", type=float, default=DEF_COS_MIN)
    args = parser.parse_args()

    with open(args.ref, encoding="utf-8") as fp:
        ref = json.load(fp)
    with open(args.test, encoding="utf-8") as fp:
        test = json.load(fp)

    print(f"基线 {ref['backend']:5s} {ref['model']}  dim={ref['dim']}  {ref['avg_ms']}ms/张")
    print(f"待验 {test['backend']:5s} {test['model']}  dim={test['dim']}  {test['avg_ms']}ms/张\n")

    if ref["dim"] != test["dim"]:
        print(f"[FAIL] 维度不一致: {ref['dim']} vs {test['dim']}")
        return 1

    only_ref = sorted(set(ref["features"]) - set(test["features"]))
    only_test = sorted(set(test["features"]) - set(ref["features"]))
    if only_ref or only_test:
        print(f"[FAIL] 图片集合不一致: 仅基线 {only_ref} / 仅待验 {only_test}")
        return 1

    print(f"{'文件':30s} {'余弦':>8s}")
    values = []
    for name in sorted(ref["features"]):
        value = cosine(ref["features"][name], test["features"][name])
        values.append((name, value))
        print(f"{name[-30:]:30s} {value:8.5f}")

    worst = min(values, key=lambda item: item[1])
    mean = sum(v for _, v in values) / len(values)
    print(f"\n余弦: 最小 {worst[1]:.5f}（{worst[0][-24:]}）  均值 {mean:.5f}")

    if worst[1] < args.cos_min:
        print(f"[FAIL] 最小值 {worst[1]:.5f} < {args.cos_min}")
        return 1
    print(f"[ OK ] 全部 ≥ {args.cos_min}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
