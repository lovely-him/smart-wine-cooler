"""柜层与采集源的对应关系。

`cam.layers` 是一段 JSON 数组，一项一层。层的 id 只是内部标识（历史记录靠它对上层），
所以删掉一层再加一层不会复用旧 id、也不会串到旧历史上；层名才是给人看的。
"""

import json
from dataclasses import dataclass

from . import bootstrap

MAX_LAYERS = 6


@dataclass(frozen=True)
class LayerConfig:
    id: str
    name: str
    backend: str
    device: str = ""
    directory: str = ""


def default_layer():
    return LayerConfig(
        id="layer1",
        name="第一层",
        backend=bootstrap.CAMERA_BACKEND,
        device=bootstrap.CAMERA_DEVICE,
        directory=str(bootstrap.CAMERA_IMAGE_DIR),
    )


def dump(layers):
    return json.dumps(
        [
            {
                "id": layer.id,
                "name": layer.name,
                "backend": layer.backend,
                "device": layer.device,
                "dir": layer.directory,
            }
            for layer in layers
        ],
        ensure_ascii=False,
    )


def default_json():
    return dump([default_layer()])


def parse(raw):
    if not raw or not str(raw).strip():
        return [default_layer()]

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"层配置不是合法 JSON: {exc}") from exc

    if not isinstance(items, list) or not items:
        raise ValueError("层配置必须是非空数组")
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
        layers.append(
            LayerConfig(
                id=layer_id,
                name=str(item.get("name") or "").strip() or f"第 {index + 1} 层",
                backend=str(item.get("backend") or "v4l2"),
                device=str(item.get("device") or ""),
                directory=str(item.get("dir") or ""),
            )
        )
    return layers
