"""采集计划：把配置翻译成采集与判定要用的参数。

这一层只读配置，不碰数据库内容也不拿模型——它回答的是「这台设备该怎么拍」。
"""

from server.platform import layers as layer_config

STABILITY_KEYS = (
    "stable_frames",
    "diff_thresh",
    "min_bright",
    "max_bright",
    "min_sharp",
    "settle_timeout",
)


class CapturePlan:
    def __init__(self, config):
        self._config = config

    @property
    def cameras(self):
        return layer_config.parse_cameras(self._config.get("cam.cameras"))

    @property
    def layers(self):
        camera_ids = {camera.id for camera in self.cameras}
        return layer_config.parse_layers(self._config.get("cam.layers"), camera_ids)

    def camera_of(self, layer):
        cameras = {camera.id: camera for camera in self.cameras}
        camera = cameras.get(layer.camera)
        if camera is None:
            raise RuntimeError(f"层「{layer.name}」引用的摄像头不在配置里")
        return camera

    @property
    def grab_cfg(self):
        return {
            "skip": int(self._config.get("cam.skip")),
            "batch": int(self._config.get("cam.batch")),
        }

    @property
    def warmup_seconds(self):
        """开流前等柜内光照稳定的秒数。"""
        return max(0.0, float(self._config.get("cam.warmup") or 0))

    def stability_cfg(self, layer):
        camera = self.camera_of(layer)
        cfg = {key: self._config.get(f"judge.{key}") for key in STABILITY_KEYS}
        cfg["grab_batch"] = int(self._config.get("cam.batch"))
        cfg["rotate"] = camera.rotate
        cfg["fov"] = camera.fov
        cfg["k1"] = camera.k1
        cfg["k2"] = camera.k2
        cfg["k3"] = camera.k3
        cfg["k4"] = camera.k4
        cfg["pan_x"] = camera.pan_x
        cfg["pan_y"] = camera.pan_y
        cfg["zoom"] = camera.zoom
        return cfg
