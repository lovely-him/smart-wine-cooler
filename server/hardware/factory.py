"""按层配置造采集源。两种后端对外接口一致，上层不关心用的是摄像头还是图片目录。"""

from .camera import V4l2Camera
from .image_dir import ImageDirCamera


def load_camera(layer, cfg):
    if layer.backend == "v4l2":
        return V4l2Camera(
            {
                "device": layer.device,
                "width": cfg["width"],
                "height": cfg["height"],
                "skip": cfg["skip"],
                "batch": cfg["batch"],
            }
        )
    if layer.backend == "image_dir":
        return ImageDirCamera({"dir": layer.directory})
    raise ValueError(f"未知采集后端: {layer.backend}")
