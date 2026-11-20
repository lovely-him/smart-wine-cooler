"""裁剪与特征融合。

同一类别会出现"正面 / 侧面 / 背面"多种外观，所以两类特征都做 L2 归一化后**加权拼接**，
检索时用"与最相似的那一张参考图"打分（多原型），而不是取该类均值——均值会落在
"既不像正面也不像背面"的中间地带。
"""

import numpy as np

from services.classify.color import color_hist

# ── 路径与常量 ──
DEF_PAD = 0.06
DEF_W_EMBED = 0.5


# ── 函数 ──
def crop_normalized(img, box, pad=DEF_PAD):
    """按归一化框裁剪，外扩 pad（相对框宽高的比例）。"""
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


def fuse(embed_vec, color_vec, w_embed=DEF_W_EMBED):
    """加权拼接后重新归一化，保证点积仍是余弦。"""
    vec = np.concatenate([w_embed * embed_vec, (1.0 - w_embed) * color_vec])
    vec = vec.astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
