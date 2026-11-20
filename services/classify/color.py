"""颜色直方图特征：纯 numpy/PIL，不需要模型，板上与宿主完全一致。

实验结论：对"深色玻璃酒瓶 / 红标可乐 / 白瓶绿盖椰汁"这几类，颜色特征的区分力比
ImageNet 深度特征更强（14/14 vs 12/14）。但它只在"类别之间颜色差异大"时有效，
所以只做**并列打分**，不替代深度特征。
"""

import numpy as np
from PIL import Image

# ── 路径与常量 ──
SIDE = 96
BINS = (12, 4, 4)


# ── 函数 ──
def color_hist(img, side=SIDE, bins=BINS):
    """HSV 三维直方图，L2 归一化后可直接点积当余弦。"""
    small = img.convert("RGB").resize((side, side), Image.BILINEAR).convert("HSV")
    arr = np.asarray(small, dtype=np.float32) / 255.0
    hist, _ = np.histogramdd(
        arr.reshape(-1, 3), bins=bins, range=((0.0, 1.0), (0.0, 1.0), (0.0, 1.0))
    )
    vec = hist.reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
