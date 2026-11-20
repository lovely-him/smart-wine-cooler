"""下载宿主侧目标检测模型（YOLOv8n，COCO 80 类）到本地缓存。

选它的理由：与板端 rknn 同系列；ONNX 直接可用，无 torch 依赖。
"""

import argparse
import os
import sys
import urllib.request

# ── 路径与常量 ──
URL = "https://huggingface.co/Kalray/yolov8n/resolve/main/yolov8n.optimized.onnx"
MIN_BYTES = 8 * 1024 * 1024


# ── 函数 ──
def download(url, path):
    tmp = path + ".part"
    with urllib.request.urlopen(url, timeout=180) as resp, open(tmp, "wb") as fp:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fp.write(chunk)
    size = os.path.getsize(tmp)
    if size < MIN_BYTES:
        os.remove(tmp)
        raise RuntimeError(f"下载不完整: {size} 字节")
    os.replace(tmp, path)
    print(f"  + {os.path.basename(path)} ({size} bytes)")


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="输出 .onnx 路径")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    download(URL, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
