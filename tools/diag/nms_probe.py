"""NMS 探测：区分"模型本身给低分"和"候选被 NMS 抑制掉"。

同一张图解码两次：iou_thres 用正常值，以及 1.0（等于关掉抑制）。
如果关掉抑制后出现了分数明显更高的候选 → 是 NMS 的问题；
如果最高分没变 → 模型本身就给这个分，调 NMS 参数没用。

用法（仓库根，宿主或板端都能跑）:
    python tools/diag/nms_probe.py <图片> <角度> [conf]
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
from services.infer import postproc
from services.infer.base import Detector

# ── 路径与常量 ──
TOP = 8
NORMAL_IOU = 0.45


# ── 函数 ──
def build(cfg, angle, conf):
    backend = cfg["infer"]["backend"]
    run_cfg = dict(cfg["infer"], rotate=angle, conf=conf)
    if backend == "rknn":
        from services.infer.rknn_backend import RknnDetector

        return RknnDetector(run_cfg)
    from services.infer.onnx_backend import OnnxDetector

    return OnnxDetector(run_cfg)


def raw_hits(detector, canvas, scale, pad_x, pad_y, width, height, conf, iou):
    """绕开 Detector.detect，用指定 iou 直接解码原始头。"""
    outputs = detector._infer(canvas)
    return postproc.decode(
        outputs, scale, pad_x, pad_y, width, height, conf, detector.class_id, iou_thres=iou
    )


def show(title, hits):
    print(f"  {title}: {len(hits)} 个候选")
    for hit in sorted(hits, key=lambda h: -h["score"])[:TOP]:
        print(f"    {hit['score']:.3f}  box={[round(v, 3) for v in hit['box']]}")


# ── main ──
def main():
    if len(sys.argv) < 3:
        print("用法: python tools/diag/nms_probe.py <图片> <角度> [conf]", file=sys.stderr)
        return 2
    path, angle = sys.argv[1], int(sys.argv[2])
    conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
    cfg = cfg_mod.load()
    with Image.open(path) as fp:
        img = fp.convert("RGB")

    work = img.rotate(angle, expand=True) if angle else img
    canvas, scale, pad_x, pad_y, _, _ = postproc.letterbox(work, cfg["infer"]["input_size"])
    width, height = work.size
    print(f"图 {path} 原图 {img.size} → 旋转后 {work.size} → 画布 scale={scale:.3f} pad=({pad_x},{pad_y})")

    detector = build(cfg, angle, conf)
    show("iou=0.45（正常）", raw_hits(detector, canvas, scale, pad_x, pad_y, width, height, conf, NORMAL_IOU))
    show("iou=1.00（关掉抑制）", raw_hits(detector, canvas, scale, pad_x, pad_y, width, height, conf, 1.0))
    detector.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
