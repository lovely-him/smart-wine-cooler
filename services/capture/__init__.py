"""采集后端的公共接口：给多少帧就返回多少帧，业务层不关心来自文件还是摄像头。"""


# ── 类 ──
class Source:
    def grab(self, count=1):
        """返回 count 张 PIL 图像（列表）。"""
        raise NotImplementedError

    def describe(self):
        raise NotImplementedError


# ── 函数 ──
def load_source(cfg):
    backend = cfg["backend"]
    if backend == "image_dir":
        from services.capture.image_dir import ImageDirSource

        return ImageDirSource(cfg)
    if backend == "v4l2":
        from services.capture.v4l2 import V4l2Source

        return V4l2Source(cfg)
    raise ValueError(f"未知的采集后端: {backend}（可选 image_dir / v4l2）")
