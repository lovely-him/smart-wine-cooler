"""按角度 + 低阈值探测：把"能不能检出"和"差多少分"分开看。

对 0/90/180/270 四个方向各跑一遍（conf 取很低），列出全部候选与分数，
再把四个方向的框映射回原图坐标做合并去重（IoU 0.5），给出"多角度合起来能拿到几个"。

用来回答：某个目标到底是"模型根本认不出"，还是"认得出但分数贴着阈值"。

用法（仓库根，宿主或板端都能跑）:
    python tools/diag/detect_probe.py <图片> [conf]
"""

import os
import sys

try:
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
MERGE_IOU = 0.5
TOP = 8


# ── 函数 ──
def build(cfg, angle, conf):
    backend = cfg["infer"]["backend"]
    run_cfg = dict(cfg["infer"], rotate=angle, conf=conf)
    if backend == "rknn":
        from services.infer.rknn_backend import RknnDetector

        return RknnDetector(run_cfg)
    from services.infer.onnx_backend import OnnxDetector

    return OnnxDetector(run_cfg)


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge(hits, thresh=MERGE_IOU):
    """各角度的框已经映射回原图坐标，这里只做去重：同位置留分数最高的。"""
    kept = []
    for hit in sorted(hits, key=lambda h: -h["score"]):
        if all(iou(hit["box"], other["box"]) < thresh for other in kept):
            kept.append(hit)
    kept.sort(key=lambda h: (h["box"][1] + h["box"][3]) / 2)
    return kept


# ── main ──
def main():
    if len(sys.argv) < 2:
        print("用法: python tools/diag/detect_probe.py <图片> [conf]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    conf = float(sys.argv[2]) if len(sys.argv) > 2 else DEF_CONF
    cfg = cfg_mod.load()
    with Image.open(path) as fp:
        img = fp.convert("RGB")

    merged = []
    print(f"后端 {cfg['infer']['backend']}  图 {path} {img.size}  conf={conf}")
    for angle in ANGLES:
        detector = build(cfg, angle, conf)
        hits = detector.detect(img)
        detector.close()
        merged.extend(hits)
        print(f"  rotate={angle:3d}  检出 {len(hits)}  {[round(h['score'], 3) for h in hits]}")

    kept = merge(merged)
    print(f"  合并去重后 {len(kept)} 个: {[round(h['score'], 3) for h in kept]}")
    for hit in kept[:TOP]:
        print(f"    box={[round(v, 3) for v in hit['box']]} score={hit['score']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
