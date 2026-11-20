"""单目标探测：把画面里每个目标逐个孤立出来，分别测它在 4 个角度下的分数。

用来把"角度敏感"和"这个目标本身难检"分开——尺度、光照、背景都不变，
只去掉旁边的其他目标。

典型用途：整图里某个目标分数很低时，判断原因是
  a) 旁边的同类目标在干扰它（孤立后分数回升），还是
  b) 这个目标本身就难检（孤立后分数仍然低）。

用法（仓库根，宿主或板端都能跑）:
    python tools/diag/single_probe.py <图片> [conf]
"""

import json
import os
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}，请用带依赖的 Python 运行", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import config as cfg_mod
from services.infer.base import Detector

# ── 路径与常量 ──
ANGLES = (0, 90, 180, 270)
DEF_CONF = 0.05


# ── 函数 ──
def build(cfg, angle, conf):
    backend = cfg["infer"]["backend"]
    run_cfg = dict(cfg["infer"], rotate=angle, conf=conf)
    if backend == "rknn":
        from services.infer.rknn_backend import RknnDetector

        return RknnDetector(run_cfg)
    from services.infer.onnx_backend import OnnxDetector

    return OnnxDetector(run_cfg)


def isolate(img, boxes, keep):
    """把 keep 以外的目标区域用背景中位数填掉，其余像素原样保留。"""
    arr = np.asarray(img, dtype=np.uint8).copy()
    height, width = arr.shape[:2]
    bg = np.median(arr[0:12, 0:12].reshape(-1, 3), axis=0).astype(np.uint8)
    for index, box in enumerate(boxes):
        if index == keep:
            continue
        x1 = max(0, int(box[0] * width))
        y1 = max(0, int(box[1] * height))
        x2 = min(width, int(box[2] * width))
        y2 = min(height, int(box[3] * height))
        arr[y1:y2, x1:x2] = bg
    return Image.fromarray(arr)


# ── main ──
def main():
    if len(sys.argv) < 2:
        print("用法: python tools/diag/single_probe.py <图片> [conf]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    conf = float(sys.argv[2]) if len(sys.argv) > 2 else DEF_CONF
    cfg = cfg_mod.load()
    with Image.open(path) as fp:
        img = fp.convert("RGB")

    ref_angle = int(cfg["infer"].get("rotate", 0) or 90)
    detector = build(cfg, ref_angle, conf)
    boxes = [hit["box"] for hit in detector.detect(img)]
    detector.close()
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)

    print(
        json.dumps(
            {
                "image": path,
                "size": list(img.size),
                "conf": conf,
                "targets": len(boxes),
                "boxes": [[round(v, 3) for v in b] for b in boxes],
            },
            ensure_ascii=False,
        )
    )
    for index, box in enumerate(boxes):
        isolated = isolate(img, boxes, index)
        scores = {}
        for angle in ANGLES:
            probe = build(cfg, angle, conf)
            hits = probe.detect(isolated)
            probe.close()
            scores[str(angle)] = round(max([h["score"] for h in hits], default=0.0), 3)
        center = [round((box[0] + box[2]) / 2, 3), round((box[1] + box[3]) / 2, 3)]
        print(f"  瓶{index} 中心{center}  0°={scores['0']:.3f}  90°={scores['90']:.3f}  "
              f"180°={scores['180']:.3f}  270°={scores['270']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
