"""在指定后端上跑一张图的检测，把结果打成 JSON。

宿主机与板子跑的是同一份代码，只是后端不同——这样比出来的差异才只反映
量化误差，而不是两边各写一套实现。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
except ImportError:
    sys.exit("缺少 pillow：pip install -r server/requirements.txt")

from server.config import bootstrap
from server.infer.detector import load_detector


def resolve(backend, conf, rotate):
    detect, rknn_model, _, _ = bootstrap.infer_setup()
    backend = backend or detect
    model = bootstrap.DETECT_MODEL_RKNN if backend == "rknn" else bootstrap.DETECT_MODEL_ONNX
    return {
        "backend": backend,
        "model": model,
        "input_size": bootstrap.DETECT_INPUT_SIZE,
        "class_id": bootstrap.DETECT_CLASS_ID,
        "conf": conf,
        "iou": bootstrap.DETECT_IOU,
        "rotate": rotate,
    }


def main():
    parser = argparse.ArgumentParser(description="单图检测")
    parser.add_argument("image")
    parser.add_argument("--backend", default="")
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--rotate", type=int, default=90)
    parser.add_argument("--out", help="写到此文件；不给则写标准输出")
    args = parser.parse_args()

    cfg = resolve(args.backend, args.conf, args.rotate)
    if not Path(cfg["model"]).exists():
        sys.stderr.write(f"模型不存在: {cfg['model']}（后端 {cfg['backend']}）\n")
        return 2

    detector = load_detector(cfg)
    try:
        with Image.open(args.image) as img:
            picture = img.convert("RGB")
            size = list(picture.size)
            detections = detector.detect(picture)
    finally:
        detector.close()

    text = json.dumps(
        {
            "image": str(args.image),
            "backend": cfg["backend"],
            "size": size,
            "detections": detections,
        },
        ensure_ascii=False,
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
