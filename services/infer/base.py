"""检测后端的公共部分：旋转、前处理、解码、坐标归一化都在这里。

子类只负责一件事——把画布喂给具体运行时并拿回原始头。这样业务层永远只看到
「归一化坐标 + 类别 + 分数」，看不到 onnxruntime 或 rknnlite。
"""

from services.infer import postproc

# ── 路径与常量 ──
DEF_CONF = 0.30


# ── 类 ──
class Detector:
    def __init__(self, cfg):
        self.conf = float(cfg.get("conf", DEF_CONF))
        self.iou = float(cfg.get("iou", postproc.DEFAULT_IOU))
        self.class_id = int(cfg.get("class_id", 0))
        self.input_size = int(cfg.get("input_size", postproc.DEFAULT_SIZE))
        self.rotate = int(cfg.get("rotate", 0))

    def _infer(self, canvas):
        raise NotImplementedError

    def detect(self, img):
        """返回 [{"cls", "score", "box": 归一化 x1,y1,x2,y2}]，按纵向中心排序。"""
        work = img.rotate(self.rotate, expand=True) if self.rotate else img
        canvas, scale, pad_x, pad_y, width, height = postproc.letterbox(work, self.input_size)
        outputs = self._infer(canvas)
        hits = postproc.decode(
            outputs, scale, pad_x, pad_y, width, height, self.conf, self.class_id, iou_thres=self.iou
        )

        full_w, full_h = img.size
        results = []
        for hit in hits:
            x1, y1, x2, y2 = postproc.to_original(hit["box"], self.rotate, full_w, full_h)
            results.append(
                {
                    "cls": self.class_id,
                    "score": round(float(hit["score"]), 4),
                    "box": [
                        round(x1 / full_w, 4),
                        round(y1 / full_h, 4),
                        round(x2 / full_w, 4),
                        round(y2 / full_h, 4),
                    ],
                }
            )
        return results

    def close(self):
        pass
