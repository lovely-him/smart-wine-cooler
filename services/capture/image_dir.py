"""目录回放采集：把一批图片当成"摄像头流"，用于宿主开发与事后复算。

不足 count 张时循环取，保证调用方总能拿到足够帧数（稳定窗需要连续帧）。
"""

import os
import time

from PIL import Image

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


# ── 类 ──
class ImageDirSource:
    def __init__(self, cfg):
        self.path = cfg["source"]
        self.delay = float(cfg.get("delay", 0.0))
        self.files = self._scan()
        self.cursor = 0

    def _scan(self):
        files = sorted(f for f in os.listdir(self.path) if f.lower().endswith(EXTS))
        if not files:
            raise RuntimeError(f"图片目录里没有图片: {self.path}")
        return files

    def grab(self, count=1):
        # 每轮重新扫目录：人工测试时替换/新增图片能立刻生效，不必重启服务
        self.files = self._scan()
        frames = []
        for _ in range(count):
            if self.delay:
                time.sleep(self.delay)
            name = self.files[self.cursor % len(self.files)]
            self.cursor += 1
            with Image.open(os.path.join(self.path, name)) as fp:
                frames.append(fp.convert("RGB"))
        return frames

    def describe(self):
        return f"image_dir {self.path}（{len(self.files)} 张）"
