"""AI 补全：拿图片 + 表单已填内容去问，只出建议，从不改任何数据。

表单已填的那几项是检索锚点——图上认不准是常态（反光、遮挡、字被框切掉），
人填的品牌、年份、条码才让它查得准。
"""

import logging
import threading
import time
from datetime import datetime

from PIL import Image

from server.platform.db import categories, fields as field_store
from server.vision import recognize

logger = logging.getLogger(__name__)

REFERENCE_SEED_LIMIT = 4


class Advisor:
    def __init__(self, db, config, image_dir, reference_dir):
        self._db = db
        self._config = config
        self._image_dir = image_dir
        self._reference_dir = reference_dir
        self._lock = threading.Lock()
        self._current = None
        self.recognizer = recognize.from_config(config)

    def reload(self):
        self.recognizer = recognize.from_config(self._config)

    def status(self):
        """界面用它显示「现在卡在哪一步、等了多久」。"""
        with self._lock:
            current = self._current
        if current is None:
            return {"running": False, "step": "", "elapsed": 0.0, "categoryId": None}
        return {
            "running": True,
            "step": current["step"],
            "elapsed": round(time.time() - current["startedAt"], 1),
            "categoryId": current["categoryId"],
        }

    def suggest(self, category_id, name, details, crop_url):
        config = self._config
        deep = bool(config.get("ai.deep"))
        defs = field_store.list_fields(self._db, category_id)
        askable = [item for item in defs if item["askable"]]
        keys = {item["key"] for item in askable}
        filled = {
            key: str(value).strip()
            for key, value in (details or {}).items()
            if key in keys and str(value).strip()
        }
        labels = {item["key"]: item["label"] for item in defs}
        known = dict(filled)
        if name:
            known = {"名称": name, **known}
        targets = [item["key"] for item in askable if item["key"] not in filled]

        paths = self._image_paths(category_id, crop_url)

        today = datetime.now().astimezone().date().isoformat()
        limit = int(config.get("ai.daily_limit") or 0)
        used = recognize.read_usage(self._db, today)
        blank = {"used": used, "limit": limit}
        if not paths:
            return {**recognize.empty_result(), **blank, "error": "没有可用的图片"}
        if not targets:
            return {**recognize.empty_result(), **blank, "error": "字段都填满了，没什么可补的"}
        if self.recognizer is None:
            return {**recognize.empty_result(), **blank, "error": "大模型没启用或没配密钥"}
        if limit > 0 and used >= limit:
            return {**recognize.empty_result(), **blank, "error": "今天问满了"}

        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))

        with self._lock:
            self._current = {
                "categoryId": category_id,
                "step": "等待模型回复",
                "startedAt": time.time(),
            }
        try:
            result = recognize.call_suggest(
                self.recognizer, images, known, targets, deep, labels=labels
            )
        finally:
            with self._lock:
                self._current = None

        if not result["error"]:
            used = recognize.bump_usage(self._db, today)
        logger.info(
            "AI 补全：类别 %s，图 %d 张，待补 %d 项 → %s（今天第 %s/%s 次）",
            category_id if category_id is not None else "新建",
            len(paths),
            len(targets),
            result["error"] or f"{len(result['suggestions'])} 项建议",
            used,
            limit or "不限",
        )
        return {**result, "used": used, "limit": limit}

    def _image_paths(self, category_id, crop_url):
        paths = []
        if crop_url:
            seed = self._image_dir / crop_url
            if seed.is_file():
                paths.append(seed)
        if category_id is not None:
            category = next(
                (item for item in categories.list_categories(self._db) if item["id"] == category_id),
                None,
            )
            for ref in (category or {}).get("refs", [])[-REFERENCE_SEED_LIMIT:]:
                ref_path = self._reference_dir / ref["path"]
                if ref_path.is_file():
                    paths.append(ref_path)
        return paths
