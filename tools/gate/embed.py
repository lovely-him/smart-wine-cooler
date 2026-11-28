"""在指定后端上算一张图的特征向量，输出 JSON。

分类全靠这个向量：如果两个后端算出来的不一样，同一批参考图在两边就会判出不同类别。
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

from server.platform import bootstrap
from server.vision.embedder import load_embedder


def main():
    parser = argparse.ArgumentParser(description="单图特征")
    parser.add_argument("image")
    parser.add_argument("--backend", default="")
    parser.add_argument("--out", help="写到此文件；不给则写标准输出")
    args = parser.parse_args()

    _, _, backend, model = bootstrap.infer_setup()
    if args.backend:
        backend = args.backend
        model = bootstrap.EMBED_MODEL_RKNN if backend == "rknn" else bootstrap.EMBED_MODEL_ONNX
    if not Path(model).exists():
        sys.stderr.write(f"模型不存在: {model}（后端 {backend}）\n")
        return 2

    embedder = load_embedder(
        {"backend": backend, "model": model, "input_size": bootstrap.EMBED_INPUT_SIZE}
    )
    try:
        with Image.open(args.image) as img:
            vector = embedder.embed(img.convert("RGB"))
    finally:
        embedder.close()

    text = json.dumps(
        {"image": str(args.image), "backend": backend, "vector": [float(v) for v in vector]},
        ensure_ascii=False,
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
