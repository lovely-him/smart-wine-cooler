"""按摄像头配置造采集源。两种后端对外接口一致，上层不关心用的是摄像头还是图片目录。"""

from .camera import V4l2Camera
from .image_dir import ImageDirCamera


def load_camera(camera, grab):
    if camera.backend == "v4l2":
        return V4l2Camera(
            {
                "device": camera.id,
                "width": camera.width,
                "height": camera.height,
                "skip": grab["skip"],
                "batch": grab["batch"],
            }
        )
    if camera.backend == "image_dir":
        return ImageDirCamera({"dir": camera.directory})
    raise ValueError(f"未知采集后端: {camera.backend}")
