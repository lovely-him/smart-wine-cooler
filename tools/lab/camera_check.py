"""连续抓帧看亮度：判断摄像头是否间歇性输出全黑（板端诊断用）。

用法（板端）: python3 tools/lab/camera_check.py [次数] [间隔秒]
"""

import subprocess
import sys
import time
from io import BytesIO

from PIL import Image, ImageStat

# ── 路径与常量 ──
DEV = "/dev/video22"
DUMP = "/tmp/camera_check.jpg"
WIDTH = 1280
HEIGHT = 720


# ── 函数 ──
def grab():
    subprocess.run(
        [
            "v4l2-ctl", "-d", DEV,
            "--set-fmt-video", f"width={WIDTH},height={HEIGHT},pixelformat=MJPG",
            "--stream-mmap", "--stream-count=1", "--stream-skip=4", "--stream-to", DUMP,
        ],
        capture_output=True,
        timeout=30,
    )
    with open(DUMP, "rb") as fp:
        raw = fp.read()
    start = raw.find(b"\xff\xd8")
    end = raw.find(b"\xff\xd9", start + 2)
    if start < 0 or end < 0:
        raise RuntimeError("没有完整 JPEG 帧")
    return Image.open(BytesIO(raw[start : end + 2])).convert("L"), len(raw)


# ── main ──
def main():
    times = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    dark = 0
    for index in range(times):
        stamp = time.strftime("%H:%M:%S")
        try:
            gray, size = grab()
            stat = ImageStat.Stat(gray)
            mean = stat.mean[0]
            marker = "  <-- 全黑" if mean < 15 else ""
            if mean < 15:
                dark += 1
            print(
                f"{stamp} 第{index + 1}次 亮度 {mean:6.1f} 标准差 {stat.stddev[0]:5.1f} "
                f"字节 {size}{marker}"
            )
        except Exception as exc:
            print(f"{stamp} 第{index + 1}次 失败 {type(exc).__name__}: {exc}")
        time.sleep(gap)
    print(f"\n{times} 次里有 {dark} 次全黑")
    return 1 if dark else 0


if __name__ == "__main__":
    sys.exit(main())
