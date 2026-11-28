"""推理组件：检测、特征、分类。

判定参数一改就得重建——模型加载时把阈值读了进去，不重建等于没改。
参考库的刷新比重建便宜，分类器单独提供一条。
"""

from server.platform import bootstrap
from server.platform.db import references
from server.vision.classifier import Classifier, ReferenceIndex
from server.vision.detector import load_detector
from server.vision.embedder import load_embedder


class Engines:
    def __init__(self, db, config):
        self._db = db
        self._config = config
        self.detector = None
        self.embedder = None
        self.classifier = None
        self.reload()

    def reload(self):
        detect_backend, detect_model, embed_backend, embed_model = bootstrap.infer_setup()
        if self.detector is not None:
            self.detector.close()
            self.embedder.close()

        self.detector = load_detector(
            {
                "backend": detect_backend,
                "model": detect_model,
                "input_size": bootstrap.DETECT_INPUT_SIZE,
                "class_id": bootstrap.DETECT_CLASS_ID,
                "conf": self._config.get("judge.conf"),
                "iou": bootstrap.DETECT_IOU,
            }
        )
        self.embedder = load_embedder(
            {
                "backend": embed_backend,
                "model": embed_model,
                "input_size": bootstrap.EMBED_INPUT_SIZE,
            }
        )
        self.classifier = Classifier(
            {"tau": self._config.get("judge.tau"), "margin": self._config.get("judge.margin")},
            ReferenceIndex.from_rows(references.reference_groups(self._db)),
            self.embedder,
        )

    def reload_references(self):
        self.classifier.index = ReferenceIndex.from_rows(
            references.reference_groups(self._db)
        )
