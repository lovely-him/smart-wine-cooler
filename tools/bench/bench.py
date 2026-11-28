"""推理耗时基准：同一份脚本在宿主与板卡上跑，结论才可比。

- backend=onnx：宿主，检测与特征都能测
- backend=rknn：板卡，只测检测（特征模型目前没有 rknn 版本）

用法:
    python bench.py --backend onnx --det-model <onnx> [--emb-model <onnx>] --images <目录>
    python bench.py --backend rknn --det-model <rknn> --images <目录>
"""

import argparse
import os
import sys
import time

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}，请用 tools/.venv 运行", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.vision import postproc

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
DEF_SIZE = 640
EMB_SIZE = 224
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ── 推理包装 ──
class OnnxRunner:
    def __init__(self, det_model, emb_model=None):
        import onnxruntime as ort

        self.det = ort.InferenceSession(det_model, providers=["CPUExecutionProvider"])
        self.det_name = self.det.get_inputs()[0].name
        self.emb = None
        if emb_model:
            self.emb = ort.InferenceSession(emb_model, providers=["CPUExecutionProvider"])
            self.emb_name = self.emb.get_inputs()[0].name

    def detect(self, canvas):
        self.det.run(None, {self.det_name: postproc.to_nchw(canvas)})

    def embed(self, img):
        arr = np.asarray(img.resize((EMB_SIZE, EMB_SIZE), Image.BILINEAR), dtype=np.float32) / 255.0
        self.emb.run(None, {self.emb_name: ((arr - MEAN) / STD).transpose(2, 0, 1)[None]})


class RknnRunner:
    def __init__(self, det_model, emb_model=None):
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(det_model) != 0:
            raise RuntimeError("load_rknn 失败")
        core = getattr(RKNNLite, "NPU_CORE_AUTO", 0)
        if self.rknn.init_runtime(core_mask=core) != 0:
            raise RuntimeError("init_runtime 失败")
        self.emb = None

    def detect(self, canvas):
        if self.rknn.inference(inputs=[canvas.astype(np.uint8)[None]]) is None:
            raise RuntimeError("rknn inference 返回 None")

    def embed(self, img):
        raise RuntimeError("rknn 后端暂不支持特征基准（特征模型没有 rknn 版本）")


# ── 函数 ──
def stats(values):
    arr = np.array(values)
    return arr.mean(), arr.min(), arr.max(), float(np.percentile(arr, 50))


def load_images(path, rotate):
    files = sorted(f for f in os.listdir(path) if f.lower().endswith(EXTS))
    frames = []
    for name in files:
        with Image.open(os.path.join(path, name)) as fp:
            img = fp.convert("RGB")
        frames.append((name, img.rotate(rotate, expand=True) if rotate else img))
    return frames


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("onnx", "rknn"))
    parser.add_argument("--det-model", required=True)
    parser.add_argument("--emb-model", default="")
    parser.add_argument("--images", required=True)
    parser.add_argument("--rotate", type=int, default=90)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--rois-per-layer", type=int, default=12, help="按每层多少个 ROI 估算整层耗时")
    args = parser.parse_args()

    if not os.path.isfile(args.det_model):
        print(f"[FAIL] 检测模型不存在: {args.det_model}", file=sys.stderr)
        return 2
    if args.emb_model and not os.path.isfile(args.emb_model):
        print(f"[FAIL] 特征模型不存在: {args.emb_model}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.images):
        print(f"[FAIL] 图片目录不存在: {args.images}", file=sys.stderr)
        return 2

    frames = load_images(args.images, args.rotate)
    if not frames:
        print(f"[FAIL] {args.images} 下没有图片", file=sys.stderr)
        return 2

    if args.backend == "rknn":
        runner = RknnRunner(args.det_model)
    else:
        runner = OnnxRunner(args.det_model, args.emb_model or None)

    canvases = [postproc.letterbox(img, DEF_SIZE)[0] for _, img in frames]

    for canvas in canvases[: args.warmup]:
        runner.detect(canvas)
    use_emb = bool(args.emb_model) and args.backend == "onnx"
    if use_emb:
        for _, img in frames[: args.warmup]:
            runner.embed(img)

    print(f"{'文件':28s} {'检测(ms)':>10s}" + (f" {'特征(ms)':>10s}" if use_emb else ""))
    dets, embs = [], []
    for (name, img), canvas in zip(frames, canvases):
        samples = []
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            runner.detect(canvas)
            samples.append((time.perf_counter() - t0) * 1000)
        dets.append(stats(samples)[3])

        line = f"{name[-28:]:28s} {dets[-1]:10.1f}"
        if use_emb:
            samples = []
            for _ in range(args.repeat):
                t0 = time.perf_counter()
                runner.embed(img)
                samples.append((time.perf_counter() - t0) * 1000)
            embs.append(stats(samples)[3])
            line += f" {embs[-1]:10.1f}"
        print(line)

    print()
    for label, values in (("检测", dets), ("特征", embs)):
        if not values:
            continue
        mean, lo, hi, p50 = stats(values)
        print(f"{label}  mean={mean:.1f}ms  p50={p50:.1f}  min={lo:.1f}  max={hi:.1f}")

    mean_det = stats(dets)[0]
    if embs:
        mean_emb = stats(embs)[0]
        layer = mean_det + args.rois_per_layer * mean_emb
        print(f"\n单帧单 ROI 合计 ≈ {mean_det + mean_emb:.1f}ms")
        print(f"一层（1 次检测 + {args.rois_per_layer} 次特征）≈ {layer:.0f}ms")
    else:
        print(f"\n单帧检测 ≈ {mean_det:.1f}ms")
    print(f"后端={args.backend}  rotate={args.rotate}  repeat={args.repeat}  图={len(frames)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
