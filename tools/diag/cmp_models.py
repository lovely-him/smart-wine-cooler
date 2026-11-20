"""多模型对比：同一批图、四个角度，比较不同 rknn 模型的检出与耗时。

换模型前用它做交叉验证：只看"检出数 + 最高分 + 单帧耗时"三项，
比在网页上肉眼观察稳得多。

用法（板端 ~/aicam，或宿主机用 onnx 模型）:
    python tools/diag/cmp_models.py <模型1> <模型2> ...
"""

import os
import sys
import time

try:
    from PIL import Image
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}，请用带依赖的 Python 运行", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import config as cfg_mod

# ── 路径与常量 ──
ANGLES = (0, 90, 180, 270)
CONF = 0.05


# ── 函数 ──
def build(cfg, model, angle):
    backend = "rknn" if model.endswith(".rknn") else "onnx"
    run_cfg = dict(cfg["infer"], backend=backend, model=model, rotate=angle, conf=CONF)
    if backend == "rknn":
        from services.infer.rknn_backend import RknnDetector

        return RknnDetector(run_cfg)
    from services.infer.onnx_backend import OnnxDetector

    return OnnxDetector(run_cfg)


# ── main ──
def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python tools/diag/cmp_models.py <模型1> <模型2> ... [图片 ...]", file=sys.stderr)
        return 2
    models = [a for a in args if a.endswith((".rknn", ".onnx"))]
    images = [a for a in args if not a.endswith((".rknn", ".onnx"))]
    if not models:
        print("[FAIL] 没给模型文件（.rknn / .onnx）", file=sys.stderr)
        return 2
    if not images:
        print("[FAIL] 没给图片", file=sys.stderr)
        return 2

    cfg = cfg_mod.load()
    for model in models:
        if not os.path.isfile(model):
            print(f"[FAIL] 模型不存在: {model}", file=sys.stderr)
            return 2
        started = time.time()
        detector = build(cfg, model, 90)
        print(f"\n=== {os.path.basename(model)}  加载 {time.time() - started:.2f}s")
        for path in images:
            with Image.open(path) as fp:
                img = fp.convert("RGB")
            cells = []
            cost = 0.0
            for angle in ANGLES:
                detector.rotate = angle
                detector.detect(img)
                started = time.time()
                hits = detector.detect(img)
                cost = (time.time() - started) * 1000
                scores = sorted([h["score"] for h in hits], reverse=True)
                cells.append(f"{angle}°:{len(scores)}({scores[0]:.2f})" if scores else f"{angle}°:0")
            print(f"  {os.path.basename(path):16s} " + "  ".join(cells) + f"   单帧 {cost:.0f}ms")
        detector.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
