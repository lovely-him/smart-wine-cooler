"""板卡侧检测后端：rknnlite（NPU）。

模型的预处理（mean 0 / std 255）已经烘进 rknn，所以这里喂 uint8 NHWC；
输出仍是 float32 原始头，解码与宿主共用 postproc。
"""

import numpy as np

from services.infer.base import Detector

# ── 类 ──


class RknnDetector(Detector):
    def __init__(self, cfg):
        super().__init__(cfg)
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(cfg["model"]) != 0:
            raise RuntimeError(f"load_rknn 失败: {cfg['model']}")
        core = getattr(RKNNLite, "NPU_CORE_AUTO", 0)
        if self.rknn.init_runtime(core_mask=core) != 0:
            raise RuntimeError("init_runtime 失败")

    def _infer(self, canvas):
        batch = canvas.astype(np.uint8)[None]
        outputs = self.rknn.inference(inputs=[batch])
        if outputs is None:
            raise RuntimeError("rknn inference 返回 None（检查输入形状与模型）")
        return outputs

    def close(self):
        self.rknn.release()
