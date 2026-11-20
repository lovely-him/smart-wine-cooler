"""特征门禁产出一侧：同一份特征后端跑两种运行时，输出可逐图比对的特征向量。

与 `detect_json.py` 同构：`--backend` 是唯一差异点。
- onnx：宿主，ImageNet 归一化后的 float32 NCHW
- rknn：板卡，uint8 NHWC（mean/std 在转换时烘进模型）
"""

import argparse
import json
import os
import sys
import time

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.infer import load_embedder

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
ROUND = 6


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("onnx", "rknn"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--images", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--input-size", type=int, default=224)
    args = parser.parse_args()

    if not os.path.isdir(args.images):
        print(f"[FAIL] 图片目录不存在: {args.images}", file=sys.stderr)
        return 2

    embedder = load_embedder(
        {"backend": args.backend, "model": args.model, "input_size": args.input_size}
    )
    files = sorted(f for f in os.listdir(args.images) if f.lower().endswith(EXTS))
    if not files:
        print(f"[FAIL] {args.images} 下没有图片", file=sys.stderr)
        return 2

    report = {"backend": args.backend, "model": os.path.basename(args.model), "features": {}}
    total_ms = 0.0
    for name in files:
        with Image.open(os.path.join(args.images, name)) as fp:
            img = fp.convert("RGB")
        t0 = time.perf_counter()
        vec = embedder.embed(img)
        elapsed = (time.perf_counter() - t0) * 1000
        total_ms += elapsed
        report["features"][name] = [round(float(v), ROUND) for v in vec]
        print(f"{name[-28:]:28s} dim={vec.shape[0]:5d}  {elapsed:7.1f}ms")

    embedder.close()
    report["dim"] = len(next(iter(report["features"].values())))
    report["avg_ms"] = round(total_ms / len(files), 2)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False)
    print(f"\n[ OK ] {len(files)} 张，维度 {report['dim']}，平均 {report['avg_ms']}ms -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
