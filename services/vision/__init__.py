"""视觉大模型能力层入口。

默认**不加载**：没开启（`vision.enabled=false`）或没凭据时连 provider 都不建，
设备不主动出网。缓存与每日上限也在这里，provider 只负责"问一次"。
"""

import hashlib
import json
import os
import time

from .base import Provider
from .noop import NoopProvider
from .siliconflow import KEY_ENV, SiliconFlow

# ── 路径与常量 ──
DEFAULT_CACHE = "cache/vision-cache.json"


# ── 类 ──
class Vision:
    """套在 provider 外面：结果缓存、每日调用上限、异常兜底。"""

    def __init__(self, provider, cache_path, daily_limit=0):
        self.provider = provider
        self.cache_path = cache_path
        self.daily_limit = int(daily_limit or 0)
        self.calls = 0
        self.hits = 0
        self.blocked = False
        self._cache = self._load()

    def _today(self):
        return time.strftime("%Y-%m-%d")

    def _load(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("date") != self._today():
                return {"date": self._today(), "calls": 0, "items": {}}
            self.calls = int(data.get("calls", 0))
            return data
        except (OSError, json.JSONDecodeError):
            return {"date": self._today(), "calls": 0, "items": {}}

    def _save(self):
        parent = os.path.dirname(os.path.abspath(self.cache_path))
        os.makedirs(parent, exist_ok=True)
        self._cache["date"] = self._today()
        self._cache["calls"] = self.calls
        with open(self.cache_path, "w", encoding="utf-8") as fp:
            json.dump(self._cache, fp, ensure_ascii=False)

    @staticmethod
    def key_of(image):
        return hashlib.sha1(f"{image.size}".encode() + image.tobytes()).hexdigest()

    def describe(self, image):
        """失败不缓存：网络抖一下留下的空结果不该被当成"这张图就是没名字"。"""
        if not self.provider.network:
            return None
        key = self.key_of(image)
        hit = self._cache["items"].get(key)
        if hit:
            self.hits += 1
            return dict(hit)
        if self.daily_limit and self.calls >= self.daily_limit:
            self.blocked = True
            return None
        result = self.provider.describe(image)
        self.calls += 1
        if result and result.get("name"):
            self._cache["items"][key] = result
        self._save()
        return result

    def close(self):
        self.provider.close()


# ── 函数 ──
def load_vision(vision_cfg, root=""):
    """返回 Vision；未开启或没凭据返回 None（调用方退化成 noop 流水线）。"""
    cfg = vision_cfg or {}
    if not cfg.get("enabled"):
        return None
    api_key = os.environ.get(KEY_ENV, "")
    provider: Provider = SiliconFlow(
        base_url=cfg.get("base_url", ""),
        api_key=api_key,
        model=cfg.get("model", ""),
        timeout=cfg.get("timeout", 30),
        max_side=cfg.get("max_side", 512),
    )
    if not provider.available():
        return None
    cache_path = cfg.get("cache") or DEFAULT_CACHE
    if not os.path.isabs(cache_path):
        cache_path = os.path.join(root, cache_path)
    return Vision(provider, cache_path, cfg.get("daily_limit", 0))


def load_vision_or_noop(vision_cfg, root="", allow_network=True):
    """批处理用：即使不能用大模型，也返回一个可用的对象，让流水线跑通。"""
    vision = load_vision(vision_cfg, root) if allow_network else None
    if vision is not None:
        return vision
    cache_path = (vision_cfg or {}).get("cache") or DEFAULT_CACHE
    if not os.path.isabs(cache_path):
        cache_path = os.path.join(root, cache_path)
    return Vision(NoopProvider(), cache_path, 0)
