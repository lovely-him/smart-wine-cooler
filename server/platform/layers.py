"""摄像头清单与柜层清单。

`cam.cameras` 描述每台采集源自己的参数，`cam.layers` 只引用摄像头。
参数跟摄像头走——同一个摄像头看多层时只有一份真值，不会出现两层朝向不一致。
两份配置分开存，保存时由 `validate` 一起校验引用完整性。
"""

import json
from dataclasses import dataclass

from . import bootstrap

MAX_LAYERS = 6
BACKENDS = ("v4l2", "image_dir")
ANGLES = (0, 90, 180, 270)


@dataclass(frozen=True)
class CameraConfig:
    id: str
    label: str
    backend: str
    width: int
    height: int
    rotate: int
    directory: str = ""
    fov: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    k4: float = 0.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    zoom: float = 4.0


@dataclass(frozen=True)
class LayerConfig:
    id: str
    name: str
    camera: str


def default_camera():
    return CameraConfig(
        id=bootstrap.CAMERA_DEVICE,
        label="摄像头 1",
        backend=bootstrap.CAMERA_BACKEND,
        width=bootstrap.CAMERA_WIDTH,
        height=bootstrap.CAMERA_HEIGHT,
        rotate=0,
        directory=str(bootstrap.CAMERA_IMAGE_DIR),
    )


def dump_cameras(cameras):
    return json.dumps(
        [
            {
                "id": camera.id,
                "label": camera.label,
                "backend": camera.backend,
                "width": camera.width,
                "height": camera.height,
                "rotate": camera.rotate,
                "dir": camera.directory,
                "fov": camera.fov,
                "k1": camera.k1,
                "k2": camera.k2,
                "k3": camera.k3,
                "k4": camera.k4,
                "panX": camera.pan_x,
                "panY": camera.pan_y,
                "zoom": camera.zoom,
            }
            for camera in cameras
        ],
        ensure_ascii=False,
    )


def dump_layers(layers):
    return json.dumps(
        [{"id": layer.id, "name": layer.name, "camera": layer.camera} for layer in layers],
        ensure_ascii=False,
    )


def default_cameras_json():
    return dump_cameras([default_camera()])


def default_layers_json():
    return dump_layers([LayerConfig(id="layer1", name="第一层", camera=default_camera().id)])


def parse_cameras(raw):
    if not raw or not str(raw).strip():
        return [default_camera()]
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError(f"摄像头配置不是合法 JSON: {exc}") from exc
    if not isinstance(items, list) or not items:
        raise ValueError("至少要有一台摄像头")

    cameras = []
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 台摄像头不是一条配置")
        camera_id = str(item.get("id") or "").strip()
        if not camera_id:
            raise ValueError(f"第 {index + 1} 台摄像头缺少设备标识")
        if camera_id in seen:
            raise ValueError(f"摄像头重复: {camera_id}")
        seen.add(camera_id)
        backend = str(item.get("backend") or "v4l2")
        if backend not in BACKENDS:
            raise ValueError(f"未知采集后端: {backend}")
        cameras.append(
            CameraConfig(
                id=camera_id,
                label=str(item.get("label") or "").strip() or camera_id.rsplit("/", 1)[-1],
                backend=backend,
                width=_positive(item.get("width"), bootstrap.CAMERA_WIDTH),
                height=_positive(item.get("height"), bootstrap.CAMERA_HEIGHT),
                rotate=_angle(item.get("rotate")),
                directory=str(item.get("dir") or ""),
                fov=_fov(item.get("fov")),
                k1=_coef(item.get("k1")),
                k2=_coef(item.get("k2")),
                k3=_coef(item.get("k3")),
                k4=_coef(item.get("k4")),
                pan_x=_pan(item.get("panX")),
                pan_y=_pan(item.get("panY")),
                zoom=_zoom(item.get("zoom")),
            )
        )
    return cameras


def parse_layers(raw, camera_ids):
    if not raw or not str(raw).strip():
        camera = default_camera()
        return [LayerConfig(id="layer1", name="第一层", camera=camera.id)]
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError(f"柜层配置不是合法 JSON: {exc}") from exc
    if not isinstance(items, list) or not items:
        raise ValueError("至少要有一层")
    if len(items) > MAX_LAYERS:
        raise ValueError(f"最多 {MAX_LAYERS} 层，现在有 {len(items)} 层")

    layers = []
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 层不是一条配置")
        layer_id = str(item.get("id") or f"layer{index + 1}")
        if layer_id in seen:
            raise ValueError(f"层 id 重复: {layer_id}")
        seen.add(layer_id)
        camera = str(item.get("camera") or "")
        if camera not in camera_ids:
            raise ValueError(f"第 {index + 1} 层引用了不存在的摄像头: {camera or '（空）'}")
        layers.append(
            LayerConfig(
                id=layer_id,
                name=str(item.get("name") or "").strip() or f"第 {index + 1} 层",
                camera=camera,
            )
        )
    return layers


def validate(cameras_raw, layers_raw):
    """两份配置合起来能站住吗；不能就报错，不让它落库。"""
    cameras = parse_cameras(cameras_raw)
    parse_layers(layers_raw, {camera.id for camera in cameras})


def _positive(value, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _angle(value):
    try:
        number = int(value) % 360
    except (TypeError, ValueError):
        return 0
    return number if number in ANGLES else 0


def _fov(value):
    """对角线视角；0 表示不矫正。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if 0 < number <= 180 else 0.0


def _coef(value):
    """畸变系数。数值范围交给用户，这里只拒绝非数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def _pan(value):
    """画面位置，-1 到 1。越界就截到边上。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, number))


def _zoom(value):
    """取景框占画布的比例倒数，1 到 4；4 是 1:1 采样。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 4.0
    return max(1.0, min(4.0, number)) if number else 4.0