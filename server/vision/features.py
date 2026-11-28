"""裁剪与特征融合：深度特征 + 颜色直方图加权拼接。

同一类别会出现正面/侧面/背面多种外观，所以检索时比「与最相似的那一张参考图」，
而不是比类均值——均值会落在"既不像正面也不像背面"的中间地带。
"""

import numpy as np
from PIL import Image

HIST_SIDE = 96
HIST_BINS = (12, 4, 4)


def color_hist(img):
    """HSV 三维直方图，L2 归一化后点积即为余弦。"""
    small = img.convert("RGB").resize((HIST_SIDE, HIST_SIDE), Image.BILINEAR).convert("HSV")
    arr = np.asarray(small, dtype=np.float32) / 255.0
    hist, _ = np.histogramdd(
        arr.reshape(-1, 3), bins=HIST_BINS, range=((0.0, 1.0), (0.0, 1.0), (0.0, 1.0))
    )
    vec = hist.reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def crop(img, box, pad):
    """按归一化框裁剪，四周外扩 pad（相对框宽高的比例）。"""
    width, height = img.size
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    left = int(max(0, (x1 - bw * pad) * width))
    top = int(max(0, (y1 - bh * pad) * height))
    right = int(min(width, (x2 + bw * pad) * width))
    bottom = int(min(height, (y2 + bh * pad) * height))
    if right - left < 2 or bottom - top < 2:
        return None
    return img.crop((left, top, right, bottom))


def fuse(embed_vec, hist_vec, w_embed):
    """加权拼接后重新归一化，保证点积仍是余弦。"""
    vec = np.concatenate([w_embed * embed_vec, (1.0 - w_embed) * hist_vec]).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
