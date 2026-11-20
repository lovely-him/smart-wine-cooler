"""扫拒识阈值：给定已标注的裁剪集，扫 tau × margin，输出"认得准 / 敢认 / 该拒"的曲线。

开放式验证协议（每个样本用两次）：
- **封闭集**：参考库含除自己以外的全部样本 → 看它能不能被认成正确类别
- **开放集**：参考库**整个去掉它的类别** → 这是真正的"没见过"，应该被拒

两个数都要看：只提高准确率会把"敢认率"压到零（全报未知），那等于没做分类。

用法:
    python -m tools.lab.scan_threshold --labels tmp/review/labels-template.json
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image
import numpy as np

from services.classify import retrieval
from services.classify.color import color_hist
from services.classify.features import fuse
from services.infer import load_embedder

# ── 路径与常量 ──
DATA_ROOT = os.path.join(ROOT, "tools", "embed_lab", "data")
TAU_STEP = 0.02
MARGIN_STEP = 0.01


# ── 函数 ──
def load_samples(labels_path, embedder, w_embed):
    with open(labels_path, encoding="utf-8") as fp:
        data = json.load(fp)
    names, labels, vectors = [], [], []
    for item in data["items"]:
        if not item.get("category"):
            continue
        path = os.path.join(DATA_ROOT, item["file"])
        if not os.path.isfile(path):
            continue
        with Image.open(path) as fp:
            img = fp.convert("RGB")
        vectors.append(fuse(embedder.embed(img), color_hist(img), w_embed))
        labels.append(item["category"])
        names.append(os.path.basename(path))
    return names, labels, vectors


def score_matrix(vectors, labels, top_k):
    """预计算每个样本的两组打分：封闭集（去掉自己）与开放集（去掉整个真类别）。"""
    closed, opened = [], []
    for i, truth in enumerate(labels):
        groups = {}
        for j, other in enumerate(labels):
            if j != i:
                groups.setdefault(other, []).append(vectors[j])
        closed.append(retrieval.category_scores(vectors[i], {k: np.stack(v) for k, v in groups.items()}, top_k))
        open_groups = {c: np.stack(v) for c, v in groups.items() if c != truth}
        opened.append(
            retrieval.category_scores(vectors[i], open_groups, top_k) if open_groups else {}
        )
    return closed, opened


def evaluate(closed, opened, labels, tau, margin):
    closed_ok = accepted = unknown_ok = 0
    for i, truth in enumerate(labels):
        guess, _ = retrieval.decide(closed[i], tau, margin, tau)
        if guess is not None:
            accepted += 1
            if guess == truth:
                closed_ok += 1
        open_guess, _ = retrieval.decide(opened[i], tau, margin, tau)
        if not opened[i] or open_guess is None:
            unknown_ok += 1
    return closed_ok, accepted, unknown_ok


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default=os.path.join(ROOT, "tmp", "review", "labels-template.json"))
    parser.add_argument("--embed-model", default=os.path.join(ROOT, "tools/embed_lab/models/mobilenetv2_pool.onnx"))
    parser.add_argument("--w-embed", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=1)
    args = parser.parse_args()

    if not os.path.isfile(args.labels):
        print(f"[FAIL] 标注文件不存在: {args.labels}", file=sys.stderr)
        return 2

    embedder = load_embedder({"backend": "onnx", "model": args.embed_model, "input_size": 224})
    names, labels, vectors = load_samples(args.labels, embedder, args.w_embed)
    embedder.close()
    total = len(labels)
    if not total:
        print("[FAIL] 没有带类别的样本", file=sys.stderr)
        return 2
    print(f"样本 {total} 张，类别分布: " + ", ".join(f"{c}×{labels.count(c)}" for c in sorted(set(labels))))
    print(f"参数: w_embed={args.w_embed} top_k={args.top_k}\n")

    closed, opened = score_matrix(vectors, labels, args.top_k)
    best = None
    print(f"{'tau':>5s} {'margin':>7s} {'认得对':>7s} {'敢认':>6s} {'该拒拒掉':>9s}")
    tau = 0.40
    while tau <= 0.90 + 1e-9:
        margin = 0.0
        while margin <= 0.25 + 1e-9:
            ok, accepted, unknown = evaluate(closed, opened, labels, round(tau, 2), round(margin, 2))
            if accepted and unknown >= total * 0.8:
                score_key = (ok / accepted, accepted, unknown)
                if best is None or score_key > best[0]:
                    best = (score_key, round(tau, 2), round(margin, 2), ok, accepted, unknown)
            if margin in (0.0, 0.05, 0.1, 0.15, 0.2):
                print(f"{tau:5.2f} {margin:7.2f} {ok:>4d}/{total:<2d} {accepted:>3d}/{total:<2d} "
                      f"{unknown:>5d}/{total:<2d}")
            margin += MARGIN_STEP
        print()
        tau += TAU_STEP

    if best is None:
        print("[FAIL] 没有任何参数组合能在「该拒拒掉 ≥80%」的前提下敢认，需要更多样本或换特征")
        return 1
    _, tau, margin, ok, accepted, unknown = best
    print(f"[ OK ] 推荐初值 tau={tau} margin={margin}  "
          f"（封闭集 {ok}/{accepted} 认得对，敢认 {accepted}/{total}，开放集拒掉 {unknown}/{total}）")
    print("       这只是 14 张样本上的初值，不代表真实精度；写进 config 后必须再人工验收")
    return 0


if __name__ == "__main__":
    sys.exit(main())
