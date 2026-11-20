"""特征后端公共部分：前处理与 L2 归一化放在这里，子类只决定"喂哪种张量"。

与检测侧同一个套路——预处理只有一份，宿主与板卡不会各写一遍导致门禁假失败。

模型 `mobilenetv2_pool.onnx` 有两个输出：`logits`(1001) 与池化特征(1280)。
我们只用**最后一个**（池化特征），与 `tools/embed_lab/lib/embed_eval.py` 的口径一致。
"""

import numpy as np
from PIL import Image

# ── 路径与常量 ──
DEF_SIZE = 224
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ── 函数 ──
def prepare(img, size=DEF_SIZE):
    """整图直接 resize（与 embed_lab 一致，不做 letterbox）。返回 uint8 HWC。"""
    return np.asarray(img.convert("RGB").resize((size, size), Image.BILINEAR), dtype=np.uint8)


def to_nchw(rgb):
    """uint8 HWC → ImageNet 归一化后的 NCHW float32（onnx 后端使用）。"""
    arr = rgb.astype(np.float32) / 255.0
    return ((arr - MEAN) / STD).transpose(2, 0, 1)[None]


# ── 类 ──
class Embedder:
    def __init__(self, cfg):
        self.input_size = int(cfg.get("input_size", DEF_SIZE))

    def _run(self, rgb):
        """rgb 是已 resize 的 uint8 HWC；返回最后一层输出（会被展平）。"""
        raise NotImplementedError

    def embed(self, img):
        """返回 L2 归一化后的一维特征（检索时直接用点积当余弦）。"""
        vec = np.asarray(self._run(prepare(img, self.input_size))).astype(np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def dim(self):
        return None

    def close(self):
        pass
