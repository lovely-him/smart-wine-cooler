"""画面质量量：不跑模型，只做 numpy 级运算。

三个量分别回答：亮不亮、清不清晰、还在不在动。它们同时用于两件事——
判断这一层能不能用，以及在抓帧过程中判断画面是否已经静止。
"""

import numpy as np
from PIL import Image

THUMB = 64
SHARP_SIDE = 256


def brightness(img):
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean())


def sharpness(img):
    """拉普拉斯响应方差，越小越糊。

    先缩到固定边长再算：不缩的话同一场景换分辨率就会得到不同的绝对值，
    阈值没法通用。
    """
    gray = img.convert("L")
    if max(gray.size) > SHARP_SIDE:
        gray = gray.resize((SHARP_SIDE, SHARP_SIDE), Image.BILINEAR)
    arr = np.asarray(gray, dtype=np.float32)
    if arr.shape[0] < 3 or arr.shape[1] < 3:
        return 0.0
    lap = (
        arr[1:-1, :-2]
        + arr[1:-1, 2:]
        + arr[:-2, 1:-1]
        + arr[2:, 1:-1]
        - 4.0 * arr[1:-1, 1:-1]
    )
    return float(lap.var())


def thumbnail(img):
    small = img.convert("L").resize((THUMB, THUMB), Image.BILINEAR)
    return np.asarray(small, dtype=np.float32)


def frame_diff(prev_thumb, thumb):
    if prev_thumb is None:
        return 0.0
    return float(np.abs(prev_thumb - thumb).mean())


def measure(img, prev_thumb=None):
    thumb = thumbnail(img)
    return {
        "bright": brightness(img),
        "sharp": sharpness(img),
        "diff": frame_diff(prev_thumb, thumb),
        "thumb": thumb,
    }
