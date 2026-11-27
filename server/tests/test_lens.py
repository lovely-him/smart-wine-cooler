"""镜头几何：去畸变能不能把弯的拉直。"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from server.hardware import lens

SIZE = (640, 480)


def distort(img, fov):
    """针孔 → 等距投影：拿来做测试输入，跟 undistort 正好互为反向。"""
    src = np.asarray(img.convert("RGB"), dtype=np.float32)
    height, width = src.shape[:2]
    f = lens.focal(width, height, fov)
    cx, cy = width / 2.0, height / 2.0
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    dx, dy = xs - cx, ys - cy
    r_out = np.hypot(dx, dy)
    theta = r_out / f
    scale = np.where(
        r_out > 1e-6, np.tan(theta) * f / np.maximum(r_out, 1e-6), 1.0
    )
    out = lens._sample(src, cx + dx * scale, cy + dy * scale)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def straight_line():
    img = Image.new("RGB", SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.line([(80, 300), (560, 300)], fill=(0, 0, 0), width=3)
    return img


def gradient():
    """平滑渐变：两次重采样的插值误差不会被噪声放大。"""
    ys, xs = np.mgrid[0 : SIZE[1], 0 : SIZE[0]]
    arr = np.stack(
        [
            xs * 255 // SIZE[0],
            ys * 255 // SIZE[1],
            np.full_like(xs, 128),
        ],
        axis=-1,
    )
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def dark_rows(img, x):
    """某一列上暗像素的行号。"""
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    return np.where(arr[:, x] < 60)[0]


def test_disabled_returns_same_image():
    img = Image.new("RGB", SIZE, (10, 20, 30))
    assert lens.undistort(img, 0) is img


def test_rotation_happens_before_undistort():
    img = Image.new("RGB", SIZE, (0, 0, 0))
    fixed = lens.fix(img, {"fov": 0, "rotate": 90})
    assert fixed.size == (SIZE[1], SIZE[0])


def test_rotation_and_undistort_commute():
    """先转再矫正 与 先矫正再转，结果应该一致——畸变径向对称，旋转绕中心。"""
    img = gradient()
    rotated_first = lens.undistort(img.rotate(90, expand=True), 160)
    warped_first = lens.undistort(img, 160).rotate(90, expand=True)
    assert rotated_first.size == warped_first.size
    diff = np.abs(
        np.asarray(rotated_first, dtype=np.float32)
        - np.asarray(warped_first, dtype=np.float32)
    ).mean()
    assert float(diff) < 3.0


def test_center_is_untouched():
    img = straight_line()
    fixed = lens.undistort(img, 160)
    assert fixed.size == SIZE
    center = np.asarray(img, dtype=np.float32)[240, 320]
    moved = np.asarray(fixed, dtype=np.float32)[240, 320]
    assert np.allclose(center, moved, atol=6)


def test_bent_line_becomes_straight():
    """等距投影里偏心的直线是弯的，去畸变后应该变直。"""
    fov = 160
    warped = distort(straight_line(), fov)
    fixed = lens.undistort(warped, fov)

    rows = [dark_rows(fixed, x) for x in (200, 320, 440)]
    rows = [item.mean() for item in rows if item.size]
    assert len(rows) == 3
    assert max(rows) - min(rows) < 3.0


def test_round_trip_keeps_center_region():
    img = straight_line()
    fov = 160
    restored = lens.undistort(distort(img, fov), fov)
    box = (160, 120, 480, 360)
    left = np.asarray(img.crop(box), dtype=np.float32)
    right = np.asarray(restored.crop(box), dtype=np.float32)
    assert float(np.abs(left - right).mean()) < 8.0


def test_overview_covers_the_whole_canvas():
    img = straight_line()
    small = lens.undistort(img, 160)
    wide = lens.overview(img, 160)
    assert small.size == SIZE
    assert wide.size == (SIZE[0] * 4, SIZE[1] * 4)


@pytest.mark.parametrize("fov", [120, 160, 180])
def test_center_view_has_no_black_corners(fov):
    """居中时源图完全盖住输出，不该有黑角。"""
    img = Image.new("RGB", SIZE, (200, 200, 200))
    fixed = lens.undistort(img, fov)
    corners = [np.asarray(fixed)[0, 0], np.asarray(fixed)[-1, -1]]
    assert all(float(pixel.mean()) > 150 for pixel in corners)


def test_pan_shifts_the_view():
    """往右偏就应该看到原图右边的东西。"""
    img = Image.new("RGB", SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([480, 0, 639, 479], fill=(0, 0, 0))
    centered = lens.undistort(img, 160)
    shifted = lens.undistort(img, 160, pan_x=1.0)
    assert float(np.asarray(centered).mean()) > float(np.asarray(shifted).mean())
