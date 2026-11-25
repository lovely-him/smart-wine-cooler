"""特征提取：整图 resize → 模型 → 取池化特征 → L2 归一化。

模型有两个输出：`logits`(1001) 与池化特征(1280)。只用**最后一个**——
与 `tools/embed_lab` 的评估口径一致，换用别的输出会让参考库整体失配。
"""

import numpy as np
from PIL import Image

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class Embedder:
    def __init__(self, cfg):
        self.input_size = int(cfg["input_size"])

    def _run(self, rgb):
        raise NotImplementedError

    def embed(self, img):
        """返回 L2 归一化后的特征，检索时点积即为余弦。"""
        rgb = np.asarray(
            img.convert("RGB").resize((self.input_size, self.input_size), Image.BILINEAR),
            dtype=np.uint8,
        )
        vec = np.asarray(self._run(rgb)).astype(np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def close(self):
        pass


class RknnEmbedder(Embedder):
    def __init__(self, cfg):
        super().__init__(cfg)
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(str(cfg["model"])) != 0:
            raise RuntimeError(f"load_rknn 失败: {cfg['model']}")
        if self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) != 0:
            raise RuntimeError("init_runtime 失败")

    def _run(self, rgb):
        outputs = self.rknn.inference(inputs=[rgb[None]])
        if not outputs:
            raise RuntimeError("rknn inference 返回空（检查输入形状与模型）")
        return outputs[-1]

    def close(self):
        self.rknn.release()


class OnnxEmbedder(Embedder):
    def __init__(self, cfg):
        super().__init__(cfg)
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(cfg["model"]), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def _run(self, rgb):
        arr = rgb.astype(np.float32) / 255.0
        tensor = ((arr - MEAN) / STD).transpose(2, 0, 1)[None]
        return self.session.run(None, {self.input_name: tensor})[-1]

    def close(self):
        self.session = None


def load_embedder(cfg):
    backend = cfg["backend"]
    if backend == "rknn":
        return RknnEmbedder(cfg)
    if backend == "onnx":
        return OnnxEmbedder(cfg)
    raise ValueError(f"未知特征后端: {backend}")
