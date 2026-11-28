"""单层观察：抓帧到静止 → 交出那一帧 → 检框 → 逐件分类。

画面不可用（太暗/太糊/一直没静止）时整层失败，不凑合一帧往下走——
用不可信的画面产出的框和裁剪图只会污染历史，比"这次没拍成"更糟。
"""

import time
from dataclasses import dataclass, field

from PIL import Image

from server.vision import stability
from server.vision.features import crop


@dataclass
class Entity:
    box: list
    score: float
    category_id: int | None
    category: str | None
    reason: str
    match: float | None
    patch: Image.Image | None = None


@dataclass
class LayerOutcome:
    status: str
    frames: int
    elapsed: float
    reasons: list[str] = field(default_factory=list)
    frame: Image.Image | None = None
    entities: list[Entity] = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "frames": self.frames,
            "elapsed": self.elapsed,
            "reasons": self.reasons,
            "size": list(self.frame.size) if self.frame else None,
            "entities": [
                {
                    "box": item.box,
                    "score": item.score,
                    "category_id": item.category_id,
                    "category": item.category,
                    "reason": item.reason,
                    "match": item.match,
                }
                for item in self.entities
            ],
        }


def observe(camera, detector, classifier, cfg):
    started = time.time()
    captured = stability.collect(camera, cfg)
    elapsed = round(time.time() - started, 3)

    if captured["status"] != "ok":
        return LayerOutcome(
            status="failed",
            frames=captured["frames"],
            elapsed=elapsed,
            reasons=captured["reasons"],
            frame=captured["last"],
        )

    frame = captured["anchor"]
    entities = []
    for hit in detector.detect(frame):
        patch = crop(frame, hit["box"], classifier.pad)
        result = (
            classifier.classify(patch)
            if patch is not None
            else {"category_id": None, "category": None, "reason": "裁剪失败"}
        )
        entities.append(
            Entity(
                box=hit["box"],
                score=hit["score"],
                category_id=result["category_id"],
                category=result["category"],
                reason=result["reason"],
                match=result.get("best_score"),
                patch=patch,
            )
        )

    return LayerOutcome(
        status="ok",
        frames=captured["frames"],
        elapsed=elapsed,
        frame=frame,
        entities=entities,
    )
