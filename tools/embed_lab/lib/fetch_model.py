"""下载评估用的 ONNX 图像模型到本地缓存。"""

import argparse
import os
import sys
import urllib.request

# ── 路径与常量 ──
MODEL_URL = (
    "https://huggingface.co/onnx-community/mobilenet_v2_1.0_224-ONNX"
    "/resolve/main/onnx/model.onnx"
)
MIN_BYTES = 10 * 1024 * 1024


# ── 函数 ──
def download(url, out):
    tmp = out + ".part"
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fp:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fp.write(chunk)
    size = os.path.getsize(tmp)
    if size < MIN_BYTES:
        os.remove(tmp)
        raise RuntimeError(f"下载不完整: {size} 字节")
    os.replace(tmp, out)
    print(f"model: {out} ({size} bytes)")


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    download(MODEL_URL, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
