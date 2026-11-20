"""host 侧检测后端：onnxruntime（CPU）。

用它做开发与基线；板卡的 rknn 后端必须与它同结论，这就是门禁在做的事。
"""

from services.infer import postproc
from services.infer.base import Detector

# ── 类 ──


class OnnxDetector(Detector):
    def __init__(self, cfg):
        super().__init__(cfg)
        import onnxruntime as ort

        self.sess = ort.InferenceSession(cfg["model"], providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name

    def _infer(self, canvas):
        return self.sess.run(None, {self.input_name: postproc.to_nchw(canvas)})
