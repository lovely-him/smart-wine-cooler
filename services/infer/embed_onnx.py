"""宿主侧特征后端：onnxruntime（CPU），用于开发与门禁基线。"""

import numpy as np

from services.infer.embed_base import Embedder, to_nchw

# ── 类 ──


class OnnxEmbedder(Embedder):
    def __init__(self, cfg):
        super().__init__(cfg)
        import onnxruntime as ort

        self.sess = ort.InferenceSession(cfg["model"], providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name
        self.output_index = len(self.sess.get_outputs()) - 1
        self.feature_dim = int(np.prod(self.sess.get_outputs()[self.output_index].shape[1:]))

    def _run(self, rgb):
        outputs = self.sess.run(None, {self.input_name: to_nchw(rgb)})
        return outputs[self.output_index]

    def dim(self):
        return self.feature_dim
