"""画面可用性：太暗、太糊、一直没静止这三种情况必须判得出来。"""

import numpy as np
import pytest
from PIL import Image

from server.vision import quality, stability


class FakeCamera:
    """按顺序吐帧；吐完就重复最后一帧。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.grabbed = 0

    def grab(self, count=None):
        count = int(count or 1)
        out = []
        for _ in range(count):
            idx = min(self.grabbed, len(self.frames) - 1)
            out.append(self.frames[idx])
            self.grabbed += 1
        return out

    def describe(self):
        return "fake"


def solid(value, size=(320, 240)):
    return Image.new("RGB", size, (value, value, value))


def noisy(seed, size=(320, 240)):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def textured(size=(320, 240)):
    """有足够高空域频率的图：清晰度够，用来只考察帧间差异。"""
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def cfg(**over):
    return {**stability.DEFAULTS, "grab_batch": 1, **over}


def test_dark_frame_measured_low():
    assert quality.brightness(solid(5)) < 15


def test_blurry_frame_measured_low():
    assert quality.brightness(solid(128)) == pytest.approx(128, abs=1)
    assert quality.sharpness(solid(128)) < 20


def test_sharp_frame_measured_high():
    assert quality.sharpness(textured()) > 20


def test_check_usable_rejects_dark():
    ok, reason = stability.check_usable(solid(3), cfg())
    assert ok is False
    assert "亮度" in reason


def test_check_usable_rejects_blurry():
    ok, reason = stability.check_usable(solid(128), cfg())
    assert ok is False
    assert "清晰度" in reason


def test_check_usable_accepts_good():
    ok, reason = stability.check_usable(textured(), cfg())
    assert ok is True
    assert reason == ""


def test_stable_scene_passes():
    frame = textured()
    camera = FakeCamera([frame] * 10)
    result = stability.collect(camera, cfg())
    assert result["status"] == "ok"
    assert result["anchor"] is not None
    assert result["frames"] == cfg()["stable_frames"]


def test_rotate_applies_to_anchor():
    camera = FakeCamera([textured()] * 10)
    result = stability.collect(camera, cfg(rotate=90))
    assert result["status"] == "ok"
    assert result["anchor"].size == (240, 320)


def test_dark_scene_times_out():
    camera = FakeCamera([solid(2)] * 10)
    result = stability.collect(camera, cfg(settle_timeout=0.01))
    assert result["status"] == "timeout"
    assert any("亮度" in reason for reason in result["reasons"])
    assert result["last"] is not None


def test_blurry_scene_times_out():
    camera = FakeCamera([solid(128)] * 10)
    result = stability.collect(camera, cfg(settle_timeout=0.01))
    assert result["status"] == "timeout"
    assert any("清晰度" in reason for reason in result["reasons"])


def test_moving_scene_times_out():
    frames = [noisy(seed) for seed in range(12)]
    camera = FakeCamera(frames)
    result = stability.collect(camera, cfg(settle_timeout=0.01))
    assert result["status"] == "timeout"
    assert any("帧间差异" in reason for reason in result["reasons"])


def test_timeout_only_counts_seconds():
    """没有帧数上限：拖得够久就能超过旧的 30 帧硬上限。"""
    counter = {"n": 0}

    class Endless:
        def grab(self, count=None):
            out = []
            for _ in range(int(count or 1)):
                out.append(noisy(counter["n"] % 1000, size=(64, 48)))
                counter["n"] += 1
            return out

        def describe(self):
            return "endless"

    result = stability.collect(Endless(), cfg(settle_timeout=0.5, grab_batch=10))
    assert result["status"] == "timeout"
    assert result["frames"] > 30


def test_anchor_is_middle_of_window():
    stable = textured()
    frames = [noisy(0), noisy(1), stable, stable, stable, stable, stable]
    camera = FakeCamera(frames)
    result = stability.collect(camera, cfg())
    assert result["status"] == "ok"
    window = result["metrics"]
    assert window[-1]["ok"] is True
