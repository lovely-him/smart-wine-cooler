"""摄像头清单与柜层清单：默认值、层数上限、引用完整性。"""

import json

import pytest

from server.config import layers


def cameras_json(*items):
    return json.dumps(list(items))


def layers_json(*items):
    return json.dumps(list(items))


def test_default_is_one_camera_and_one_layer():
    cameras = layers.parse_cameras("")
    parsed = layers.parse_layers("", {camera.id for camera in cameras})
    assert len(cameras) == 1
    assert len(parsed) == 1
    assert parsed[0].name == "第一层"
    assert parsed[0].camera == cameras[0].id


def test_default_json_round_trips():
    cameras = layers.parse_cameras(layers.default_cameras_json())
    parsed = layers.parse_layers(layers.default_layers_json(), {c.id for c in cameras})
    assert layers.dump_cameras(cameras) == layers.default_cameras_json()
    assert layers.dump_layers(parsed) == layers.default_layers_json()


def test_new_camera_starts_at_zero_degrees():
    assert layers.parse_cameras("")[0].rotate == 0


def test_too_many_layers_rejected():
    raw = layers_json(*[{"id": f"L{i}", "camera": "cam"} for i in range(layers.MAX_LAYERS + 1)])
    with pytest.raises(ValueError, match="最多"):
        layers.parse_layers(raw, {"cam"})


def test_duplicate_layer_id_rejected():
    raw = layers_json({"id": "same", "camera": "cam"}, {"id": "same", "camera": "cam"})
    with pytest.raises(ValueError, match="重复"):
        layers.parse_layers(raw, {"cam"})


def test_layer_pointing_at_unknown_camera_rejected():
    with pytest.raises(ValueError, match="不存在的摄像头"):
        layers.parse_layers(layers_json({"id": "L1", "camera": "ghost"}), {"cam"})


def test_whole_pair_validated_together():
    cameras = cameras_json({"id": "cam", "label": "c"})
    assert layers.validate(cameras, layers_json({"id": "L1", "camera": "cam"})) is None
    with pytest.raises(ValueError, match="不存在的摄像头"):
        layers.validate(cameras, layers_json({"id": "L1", "camera": "ghost"}))


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="未知采集后端"):
        layers.parse_cameras(cameras_json({"id": "cam", "label": "c", "backend": "hdmi"}))


def test_blank_name_falls_back_to_position():
    parsed = layers.parse_layers(layers_json({"id": "a", "name": "   ", "camera": "cam"}), {"cam"})
    assert parsed[0].name == "第 1 层"


def test_broken_json_is_reported():
    with pytest.raises(ValueError, match="不是合法 JSON"):
        layers.parse_cameras("[{")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        layers.parse_layers("[{", {"cam"})
