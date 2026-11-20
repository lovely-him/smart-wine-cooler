"""单帧跑通脚本：采集 → 推理 → 打印结果，用来验证后端链路。

用法:
    python -m services.infer.run_once --camera layer1 [--config config/app.json] [--repeat 5]
"""

import argparse
import sys
import time

from services import config as cfg_mod
from services.capture import load_source
from services.infer import load_detector


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    parser.add_argument("--camera", default="")
    parser.add_argument("--repeat", type=int, default=1, help="重复推理次数，用于看耗时")
    args = parser.parse_args()

    try:
        cfg = cfg_mod.load(args.config)
        cam = cfg_mod.camera(cfg, args.camera) if args.camera else cfg["cameras"][0]
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    try:
        source = load_source(cam["capture"])
        detector = load_detector(cfg["infer"])
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    print(f"摄像头: {cam['id']} {cam['name']}")
    print(f"  采集: {source.describe()}")
    print(f"  推理: {cfg['infer']['backend']} {cfg['infer']['model']}")

    t0 = time.perf_counter()
    frames = source.grab(1)
    print(f"  采集耗时: {(time.perf_counter() - t0) * 1000:.0f}ms")
    img = frames[0]

    results = []
    for idx in range(max(1, args.repeat)):
        t0 = time.perf_counter()
        results = detector.detect(img)
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  第 {idx + 1} 次推理: {elapsed:.1f}ms  -> {len(results)} 个")

    w, h = img.size
    print(f"\n画面 {w}x{h}，检出 {len(results)} 个：")
    for i, det in enumerate(results, start=1):
        x1, y1, x2, y2 = det["box"]
        print(
            f"  #{i} score={det['score']:.3f} "
            f"归一化=({x1:.3f},{y1:.3f})-({x2:.3f},{y2:.3f}) "
            f"像素=({x1 * w:.0f},{y1 * h:.0f})-({x2 * w:.0f},{y2 * h:.0f})"
        )

    detector.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
