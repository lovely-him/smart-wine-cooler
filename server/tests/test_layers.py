"""层配置：默认值、增删上限、id 不许重复。"""

import json

import pytest

from server.config import layers


def test_default_is_one_layer():
    parsed = layers.parse("")
    assert len(parsed) == 1
    assert parsed[0].name == "第一层"


def test_default_json_round_trips():
    raw = layers.default_json()
    assert layers.dump(layers.parse(raw)) == raw


def test_too_many_layers(temp=False):
    item = {"id": "x", "name": "层"}
    raw = json.dumps([{"id": f"L{i}", "name": "层"} for i in range(layers.MAX_LAYERS + 1)])
    with pytest.raises(ValueError, match="最多"):
        layers.parse(raw)


def test_duplicate_id_rejected():
    with pytest.raises(ValueError, match="重复"):
        layers.parse(json.dumps([{"id": "same"}, {"id": "same"}]))


def test_blank_name_falls_back_to_position():
    parsed = layers.parse(json.dumps([{"id": "a", "name": "   "}]))
    assert parsed[0].name == "第 1 层"


def test_broken_json_is_reported():
    with pytest.raises(ValueError, match="不是合法 JSON"):
        layers.parse("[{")
