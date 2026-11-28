"""镜头几何：先去畸变，再按安装方向旋转。

去畸变是从「理想图」反查「畸变图」的位置，所以不需要迭代求解：
输出像素 → 理想方向 → 等距投影落点 → 源坐标。

超广角镜头矫正后视野必然变窄：针孔投影 r = f·tanθ 在 θ 接近 90° 时发散，
源图边缘的方向在矫正后会被推到极远处，永远塞不进一张固定尺寸的图里。
所以输出只覆盖源图中间一块，看哪一块由 pan 决定。

pan 的可移动范围按一块 4 倍于采集分辨率的画布算：画布保持源图的宽高比，
四角会露出黑边——那本来就没拍到，黑边就是在告诉你「这里没内容」。
"""

import numpy as np
from PIL import Image

ROWS_PER_BLOCK = 128
CANVAS_SCALE = 4.0
OVERVIEW_WIDTH = 640


def focal(width, height, fov):
    """由对角线视角算焦距（像素）。"""
    return (float(np.hypot(width, height)) / 2.0) / (float(np.radians(fov)) / 2.0)


def undistort(img, fov, k1=0.0, k2=0.0, k3=0.0, k4=0.0, pan_x=0.0, pan_y=0.0, zoom=4.0):
    """把等距投影的广角画面拉成针孔投影，输出尺寸与输入相同。

    `zoom` 是取景框在画布上占的比例倒数：4 表示框占画布 1/4（1:1 采样，最清晰），
    1 表示框占满画布（视野最广，但输出要把 4 倍范围塞进同样多的像素，很糊）。
    """
    if not fov or float(fov) <= 0:
        return img

    src = np.asarray(img.convert("RGB"), dtype=np.float32)
    height, width = src.shape[:2]
    canvas_w = width * CANVAS_SCALE
    canvas_h = height * CANVAS_SCALE
    span = CANVAS_SCALE / max(1.0, min(CANVAS_SCALE, float(zoom)))
    span_w = width * span
    span_h = height * span
    warped = _warp(
        src,
        focal(width, height, float(fov)),
        (k1, k2, k3, k4),
        (
            float(pan_x) * (canvas_w - span_w) / 2.0,
            float(pan_y) * (canvas_h - span_h) / 2.0,
        ),
        span_w,
        width,
        height,
    )
    return _to_image(warped)


def overview(img, fov, k1=0.0, k2=0.0, k3=0.0, k4=0.0):
    """完整矫正画布，按 1:1 像素生成，不缩放。"""
    src = np.asarray(img.convert("RGB"), dtype=np.float32)
    height, width = src.shape[:2]
    if not fov or float(fov) <= 0:
        return _to_image(src)

    return _to_image(
        _warp(
            src,
            focal(width, height, float(fov)),
            (k1, k2, k3, k4),
            (0.0, 0.0),
            float(width) * CANVAS_SCALE,
            int(width * CANVAS_SCALE),
            int(height * CANVAS_SCALE),
        )
    )


def _warp(src, f, coeffs, center, span_w, out_w, out_h):
    """把源图重采样成 out_w×out_h。

    `center` 是输出中心在源图坐标系里相对画面中心的偏移，
    `span_w` 是输出宽度覆盖源图多少像素——两者一起决定「看哪、看多大」。
    """
    k1, k2, k3, k4 = coeffs
    cx, cy = src.shape[1] / 2.0, src.shape[0] / 2.0
    step = span_w / out_w
    xs = (np.arange(out_w, dtype=np.float32) - out_w / 2.0) * step + center[0]
    ys = (np.arange(out_h, dtype=np.float32) - out_h / 2.0) * step + center[1]
    out = np.zeros((out_h, out_w, src.shape[2]), dtype=np.float32)

    for start in range(0, out_h, ROWS_PER_BLOCK):
        stop = min(start + ROWS_PER_BLOCK, out_h)
        x = xs[None, :] / f
        y = ys[start:stop, None] / f
        r = np.hypot(x, y)
        theta = np.arctan(r)
        t2 = theta * theta
        theta_d = theta * (1.0 + k1 * t2 + k2 * t2**2 + k3 * t2**3 + k4 * t2**4)
        shrink = np.where(r > 1e-6, theta_d / np.maximum(r, 1e-6), 1.0)
        out[start:stop] = _sample(
            src, cx + xs[None, :] * shrink, cy + ys[start:stop, None] * shrink
        )
    return out


def _sample(src, map_x, map_y):
    """双线性重采样；落到源图外面的像素取黑。"""
    height, width = src.shape[:2]
    x0 = np.floor(map_x).astype(np.int32)
    y0 = np.floor(map_y).astype(np.int32)
    fx = (map_x - x0)[..., None]
    fy = (map_y - y0)[..., None]

    inside = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < width) & (y0 + 1 < height)
    x0 = np.clip(x0, 0, width - 2)
    y0 = np.clip(y0, 0, height - 2)
    x1 = x0 + 1
    y1 = y0 + 1

    top = src[y0, x0] * (1.0 - fx) + src[y0, x1] * fx
    bottom = src[y1, x0] * (1.0 - fx) + src[y1, x1] * fx
    blended = top * (1.0 - fy) + bottom * fy
    return blended * inside[..., None]


def _to_image(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def fix(img, cfg):
    """按采集配置先转安装方向，再纠正镜头畸变。

    两步互不知道对方：畸变径向对称，旋转是绕中心的 90° 整数倍，可交换。
    所以取景框的位置天然就是最终画面的方向，不需要任何坐标换算。
    """
    rotate = int(cfg.get("rotate") or 0)
    if rotate:
        img = img.rotate(rotate, expand=True)
    return undistort(
        img,
        cfg.get("fov") or 0,
        cfg.get("k1") or 0,
        cfg.get("k2") or 0,
        cfg.get("k3") or 0,
        cfg.get("k4") or 0,
        cfg.get("pan_x") or 0,
        cfg.get("pan_y") or 0,
        cfg.get("zoom") or 4,
    )
