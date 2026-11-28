"""层内次序：按框中心 x、再中心 y。位置是算出来的，这里验它算得对且可复现。"""

from server.kernel import ordering


def test_key_uses_center_x_first():
    left = {"x": 0.10, "y": 0.50, "w": 0.05, "h": 0.20}
    right = {"x": 0.60, "y": 0.10, "w": 0.05, "h": 0.20}
    assert ordering.key_xywh(left) < ordering.key_xywh(right)


def test_key_breaks_tie_by_center_y():
    high = {"x": 0.20, "y": 0.10, "w": 0.10, "h": 0.10}
    low = {"x": 0.20, "y": 0.60, "w": 0.10, "h": 0.10}
    assert ordering.key_xywh(high) < ordering.key_xywh(low)


def test_ltrb_and_xywh_agree():
    box = {"x": 0.25, "y": 0.40, "w": 0.20, "h": 0.10}
    as_ltrb = [box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]]
    assert ordering.key_xywh(box) == ordering.key_ltrb(as_ltrb)


def test_order_is_reproducible_regardless_of_input_order():
    boxes = [
        {"x": 0.50, "y": 0.10, "w": 0.10, "h": 0.10},
        {"x": 0.10, "y": 0.90, "w": 0.10, "h": 0.10},
        {"x": 0.10, "y": 0.10, "w": 0.10, "h": 0.10},
    ]
    once = sorted(boxes, key=ordering.key_xywh)
    twice = sorted(reversed(boxes), key=ordering.key_xywh)
    assert once == twice
    assert once[0]["x"] == 0.10 and once[0]["y"] == 0.10
