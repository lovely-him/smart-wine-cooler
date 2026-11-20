"""精度门禁用检测脚本：同一份后处理，跑两种推理后端，产出可逐图比对的结果。

- 后端 onnx：宿主侧，输入 float32 NCHW（0~1）
- 后端 rknn：板端，输入 uint8 NHWC（归一化已烘进模型：mean 0 / std 255）

两者输出统一为"原图坐标下的框 + 分数 + 类别"，便于直接比对。

用法:
    python detect_json.py --backend onnx|rknn --model <path> --images <目录> --out <json>
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

from services.infer import postproc

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# ── 推理后端 ──
class OnnxBackend:
    name = "onnx"

    def __init__(self, model):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name

    def run(self, canvas, size):
        return self.sess.run(None, {self.input_name: postproc.to_nchw(canvas)})


class RknnBackend:
    name = "rknn"

    def __init__(self, model):
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(model) != 0:
            raise RuntimeError("load_rknn 失败")
        core = getattr(RKNNLite, "NPU_CORE_AUTO", 0)
        if self.rknn.init_runtime(core_mask=core) != 0:
            raise RuntimeError("init_runtime 失败")

    def run(self, canvas, size):
        batch = canvas.astype(np.uint8)[None]
        result = self.rknn.inference(inputs=[batch])
        if result is None:
            raise RuntimeError("rknn inference 返回 None（检查输入形状/模型）")
        return result


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, choices=("onnx", "rknn"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--images", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--conf", type=float, default=0.15)
    parser.add_argument("--class-id", type=int, default=39)
    parser.add_argument("--rotate", type=int, default=90)
    parser.add_argument("--input-size", type=int, default=postproc.DEFAULT_SIZE)
    parser.add_argument("--dump-canvas", default="", help="把预处理后的画布存到该目录（校准图用）")
    args = parser.parse_args()

    if not os.path.isdir(args.images):
        print(f"[FAIL] 图片目录不存在: {args.images}", file=sys.stderr)
        return 2

    backend = OnnxBackend(args.model) if args.backend == "onnx" else RknnBackend(args.model)
    files = sorted(f for f in os.listdir(args.images) if f.lower().endswith(EXTS))
    if not files:
        print(f"[FAIL] {args.images} 下没有图片", file=sys.stderr)
        return 2

    report = {"backend": args.backend, "model": os.path.basename(args.model),
              "conf": args.conf, "rotate": args.rotate, "images": []}

    for name in files:
        with Image.open(os.path.join(args.images, name)) as fp:
            img = fp.convert("RGB")
        width, height = img.size
        work = img.rotate(args.rotate, expand=True) if args.rotate else img
        canvas, scale, pad_x, pad_y, _, _ = postproc.letterbox(work, args.input_size)

        if args.dump_canvas:
            os.makedirs(args.dump_canvas, exist_ok=True)
            Image.fromarray(canvas.astype(np.uint8)).save(
                os.path.join(args.dump_canvas, os.path.splitext(name)[0] + ".png")
            )

        t0 = time.perf_counter()
        outputs = backend.run(canvas, args.input_size)
        elapsed = (time.perf_counter() - t0) * 1000

        hits = postproc.decode(outputs, scale, pad_x, pad_y, width, height, args.conf, args.class_id)
        for hit in hits:
            box = [round(v, 1) for v in hit["box"]]
            hit["score"] = round(hit["score"], 4)
            hit["box"] = [round(v, 1) for v in postproc.to_original(box, args.rotate, width, height)]
        hits.sort(key=lambda h: (h["box"][1] + h["box"][3]) / 2)

        report["images"].append(
            {"file": name, "width": width, "height": height, "infer_ms": round(elapsed, 1),
             "count": len(hits), "detections": hits}
        )
        print(f"{name[-24:]:24s} {elapsed:7.1f}ms  {len(hits)} 个")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    avg = sum(i["infer_ms"] for i in report["images"]) / len(report["images"])
    total = sum(i["count"] for i in report["images"])
    print(f"\n[ OK ] {len(files)} 张，共 {total} 个，平均推理 {avg:.1f}ms -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
