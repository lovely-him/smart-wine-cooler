"""图片目录采集源。

用在两个地方：宿主机没有摄像头时的开发验证，以及还没装摄像头的柜层。
同一层的图轮转取，所以静止判定在它上面天然成立。
"""

from pathlib import Path

from PIL import Image

SUFFIXES = {".jpg", ".jpeg", ".png"}


class ImageDirCamera:
    def __init__(self, cfg):
        self.dir = Path(cfg["dir"])
        if not self.dir.is_dir():
            raise RuntimeError(f"图片目录不存在: {self.dir}")
        self.paths = self._scan()
        self._cursor = 0

    def _scan(self):
        paths = sorted(p for p in self.dir.iterdir() if p.suffix.lower() in SUFFIXES)
        if not paths:
            raise RuntimeError(f"图片目录里没有可用图片: {self.dir}")
        return paths

    def grab(self, count=None):
        # 每次重扫：手工测试时换图能立刻生效，不必重启服务
        self.paths = self._scan()
        count = int(count or 1)
        frames = []
        for _ in range(count):
            path = self.paths[self._cursor % len(self.paths)]
            self._cursor += 1
            with Image.open(path) as img:
                frames.append(img.convert("RGB").copy())
        return frames

    def describe(self):
        return f"image_dir {self.dir}（{len(self.paths)} 张）"
