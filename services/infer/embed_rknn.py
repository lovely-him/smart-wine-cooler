"""板卡侧特征后端：rknnlite（NPU）。

ImageNet 的 mean/std 已经烘进 rknn（转换时用 `--mean 123.675,116.28,103.53` /
`--std 58.395,57.12,57.375`），所以这里直接喂 uint8 NHWC。
"""

import numpy as np

from services.infer.embed_base import Embedder

# ── 类 ──


class RknnEmbedder(Embedder):
    def __init__(self, cfg):
        super().__init__(cfg)
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(cfg["model"]) != 0:
            raise RuntimeError(f"load_rknn 失败: {cfg['model']}")
        core = getattr(RKNNLite, "NPU_CORE_AUTO", 0)
        if self.rknn.init_runtime(core_mask=core) != 0:
            raise RuntimeError("init_runtime 失败")

    def _run(self, rgb):
        outputs = self.rknn.inference(inputs=[rgb[None]])
        if outputs is None:
            raise RuntimeError("rknn inference 返回 None（检查输入形状与模型）")
        return outputs[-1]

    def dim(self):
        return None

    def close(self):
        self.rknn.release()
