"""检测：letterbox → 推理 → 解码 → 归一化坐标。

业务层只看得到「归一化坐标 + 分数」，看不到 rknnlite 或 onnxruntime。
两个后端必须共用同一份后处理，否则跨后端比出来的差异会被误当成量化误差。
"""

from server import ordering

from . import postproc


class Detector:
    def __init__(self, cfg):
        self.conf = float(cfg["conf"])
        self.iou = float(cfg["iou"])
        self.class_id = int(cfg["class_id"])
        self.input_size = int(cfg["input_size"])

    def _infer(self, canvas):
        raise NotImplementedError

    def detect(self, img):
        """返回 [{"score", "box": 归一化 x1,y1,x2,y2}]，按框中心 x 排序（层内位置）。"""
        canvas, scale, pad_x, pad_y = postproc.letterbox(img, self.input_size)
        hits = postproc.decode(
            self._infer(canvas),
            scale,
            pad_x,
            pad_y,
            img.size[0],
            img.size[1],
            self.conf,
            self.class_id,
            self.iou,
        )

        full_w, full_h = img.size
        results = []
        for hit in hits:
            x1, y1, x2, y2 = hit["box"]
            results.append(
                {
                    "score": round(float(hit["score"]), 4),
                    "box": [
                        round(x1 / full_w, 4),
                        round(y1 / full_h, 4),
                        round(x2 / full_w, 4),
                        round(y2 / full_h, 4),
                    ],
                }
            )
        results.sort(key=lambda r: ordering.key_ltrb(r["box"]))
        return results

    def close(self):
        pass


class RknnDetector(Detector):
    def __init__(self, cfg):
        super().__init__(cfg)
        from rknnlite.api import RKNNLite

        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(str(cfg["model"])) != 0:
            raise RuntimeError(f"load_rknn 失败: {cfg['model']}")
        if self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) != 0:
            raise RuntimeError("init_runtime 失败")

    def _infer(self, canvas):
        import numpy as np

        # 预处理（mean 0 / std 255）已经烘进 rknn 模型，这里喂 uint8 NHWC。
        # 喂 float 的 NCHW 不会报错，只会得到全错的框。
        outputs = self.rknn.inference(inputs=[canvas.astype(np.uint8)[None]])
        if outputs is None:
            raise RuntimeError("rknn inference 返回 None（检查输入形状与模型）")
        return outputs

    def close(self):
        self.rknn.release()


class OnnxDetector(Detector):
    def __init__(self, cfg):
        super().__init__(cfg)
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(cfg["model"]), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def _infer(self, canvas):
        return self.session.run(None, {self.input_name: postproc.to_nchw(canvas)})

    def close(self):
        self.session = None


def load_detector(cfg):
    backend = cfg["backend"]
    if backend == "rknn":
        return RknnDetector(cfg)
    if backend == "onnx":
        return OnnxDetector(cfg)
    raise ValueError(f"未知检测后端: {backend}")
