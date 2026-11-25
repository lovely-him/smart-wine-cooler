"""应用级装配：一个进程内持有一份数据库、配置、模型与触发调度。

服务是单进程多线程：Web 应答在事件循环里，观察跑在独立线程——一次观察要跑好几秒，
放在事件循环里界面会全程无响应。写库统一走 `Database.write` 的写锁。

实时状态（摄像头能不能开、存储剩多少、门什么状态）**每次现查，不落盘**。
"""

import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from server.config import ConfigStore, bootstrap, layers as layer_config
from server.db import Database, fields as field_store, repo
from server.events import EventBus
from server.hardware import storage
from server.hardware import network
from server.hardware.light import MockLight, load_light
from server.infer import recognize
from server.infer.classifier import Classifier, ReferenceIndex
from server.infer.detector import load_detector
from server.infer.embedder import load_embedder
from server import logs, retention
from server.pipeline.scan import run_scan

logger = logging.getLogger(__name__)

STABILITY_KEYS = (
    "stable_frames",
    "diff_thresh",
    "min_bright",
    "max_bright",
    "min_sharp",
    "max_frames",
    "settle_timeout",
)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _local_ip():
    """本机在这个局域网里的地址。UDP connect 只查路由，不发包。"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


class Runtime:
    def __init__(self, db_path=None, image_dir=None):
        self.db = Database(db_path or bootstrap.DB_PATH)
        self.db.init_schema()
        field_store.ensure_seeded(self.db)
        self.config = ConfigStore(self.db)
        self.image_dir = Path(image_dir or bootstrap.IMAGE_DIR)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.reference_dir = Path(bootstrap.REFERENCE_DIR)
        self.reference_dir.mkdir(parents=True, exist_ok=True)

        self.detector = None
        self.embedder = None
        self.classifier = None
        self._reload_inference()
        self.recognizer = recognize.from_config(self.config)
        self._recognition = None
        self._recognition_lock = threading.Lock()
        self.bus = EventBus()
        self.light = None
        self.door = None
        self._observe_lock = threading.Lock()
        self._observing = False
        self._scan_started_at = None
        self._rebuild_light()

        self.cancel_check = lambda: False
        latest = repo.latest_observation(self.db)
        self._last_observation_id = latest["id"] if latest else None
        self.started_at = time.time()
        logs.set_level(self.config.get("sys.log_level"))

        dropped = self.reclaim_orphans()
        if dropped:
            logger.info("清掉 %d 个没记录指向的图片目录", dropped)

    # ── 模型 ──
    def _reload_inference(self):
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
                "conf": self.config.get("judge.conf"),
                "iou": bootstrap.DETECT_IOU,
            }
        )
        self.embedder = load_embedder(
            {"backend": embed_backend, "model": embed_model, "input_size": bootstrap.EMBED_INPUT_SIZE}
        )
        self.classifier = Classifier(
            {"tau": self.config.get("judge.tau"), "margin": self.config.get("judge.margin")},
            ReferenceIndex.from_rows(repo.reference_groups(self.db)),
            self.embedder,
        )

    def reload_references(self):
        self.classifier.index = ReferenceIndex.from_rows(repo.reference_groups(self.db))

    # ── 配置派生 ──
    @property
    def layers(self):
        return layer_config.parse(self.config.get("cam.layers"))

    @property
    def camera_cfg(self):
        return {
            "width": int(self.config.get("cam.width")),
            "height": int(self.config.get("cam.height")),
            "skip": int(self.config.get("cam.skip")),
            "batch": int(self.config.get("cam.batch")),
        }

    @property
    def stability_cfg(self):
        cfg = {key: self.config.get(f"judge.{key}") for key in STABILITY_KEYS}
        cfg["grab_batch"] = int(self.config.get("cam.batch"))
        cfg["rotate"] = int(self.config.get("cam.rotate") or 0)
        return cfg

    # ── 观察 ──
    def is_observing(self):
        return self._observing

    def claim_observe(self):
        """原子地占住「正在观察」。已占用返回 False。"""
        with self._observe_lock:
            if self._observing:
                return False
            self._observing = True
            return True

    def start_observe(self, trigger="manual"):
        """在后台线程跑一次观察，立即返回是否受理。"""
        if not self.claim_observe():
            return False
        self.sync_light()
        threading.Thread(
            target=self._observe_worker, args=(trigger,), daemon=True, name="observe"
        ).start()
        return True

    def observe(self, trigger="manual"):
        """同步跑一次整柜观察。已在观察中则直接返回 None——不排队、不打断。"""
        if not self.claim_observe():
            return None
        return self._run_observe(trigger)

    def _observe_worker(self, trigger):
        try:
            self._run_observe(trigger)
        except Exception:
            logger.exception("观察失败")
        finally:
            self.db.drop_thread_conn()

    def _run_observe(self, trigger):
        started = _now()
        self._scan_started_at = started
        obs_id = None
        status = "void"
        self.bus.publish("observe.start", {"trigger": trigger, "startedAt": started})
        try:
            obs_id, status = run_scan(self, trigger, cancel=self.cancel_check)
            self._last_observation_id = obs_id
            self.trim_observations()
            return {"observation_id": obs_id, "status": status}
        finally:
            self._scan_started_at = None
            self._observing = False
            self.sync_light()
            self.bus.publish("observe.done", {"observationId": obs_id, "status": status})

    # ── 实时状态 ──
    def live_status(self):
        cameras = []
        for layer in self.layers:
            try:
                from server.hardware import factory

                probe = factory.load_camera(layer, self.camera_cfg)
                available = True
                del probe
            except Exception:
                available = False
            cameras.append({"id": layer.id, "available": available, "layers": [layer.id]})

        usage = storage.usage(self.image_dir)
        door = self.door_state()
        return {
            "cameras": cameras,
            "storageFreeBytes": usage["free"],
            "storageTotalBytes": usage["total"],
            "observing": self._observing,
            "scanStartedAt": self._scan_started_at,
            "lastObservationId": self._last_observation_id,
            "door": None if door == "unknown" else door,
            "doorLevel": self.door_level(),
            "light": self.light_state(),
            "now": _now(),
            "uptimeSeconds": int(time.time() - self.started_at),
            "ip": _local_ip(),
            "storageLow": self.storage_low(),
        }

    def apply_settings(self, group, values, passcode):
        """写入一组配置。危险项要过口令；判定类改了就地重建推理组件。"""
        self.config.guard(values, passcode)
        changed, need_service, need_device = self.config.set_many(group, values)
        if any(key.startswith("trig.") for key in changed):
            self._rebuild_light()
        if any(key.startswith("ai.") for key in changed):
            self.recognizer = recognize.from_config(self.config)
        if "sys.log_level" in changed:
            logs.set_level(self.config.get("sys.log_level"))
        if "net.hostname" in changed:
            network.set_hostname(self.config.get("net.hostname"))
        if any(key.startswith("judge.") for key in changed):
            if self._observing:
                raise PermissionError("正在观察，等这次结束再改判定参数")
            self._reload_inference()
        return changed, need_service, need_device

    def storage_low(self):
        floor = float(self.config.get("data.min_free_mb") or 0) * 1024 * 1024
        return storage.usage(self.image_dir)["free"] < floor

    # ── AI 补全 ──
    def recognition_status(self):
        """界面用它显示"现在卡在哪一步、等了多久"。"""
        with self._recognition_lock:
            current = self._recognition
        if current is None:
            return {"running": False, "step": "", "elapsed": 0.0, "categoryId": None}
        return {
            "running": True,
            "step": current["step"],
            "elapsed": round(time.time() - current["startedAt"], 1),
            "categoryId": current["categoryId"],
        }

    def _set_recognition_step(self, step):
        with self._recognition_lock:
            if self._recognition is not None:
                self._recognition["step"] = step

    def suggest_category(self, category_id, name, details, crop_url):
        """拿图片 + 表单已填内容问大模型，返回空字段的建议。从不改任何数据。"""
        from PIL import Image

        deep = bool(self.config.get("ai.deep"))
        targets_all = recognize.ASKABLE_KEYS
        filled = {
            key: str(value).strip()
            for key, value in (details or {}).items()
            if key in targets_all and str(value).strip()
        }
        known = dict(filled)
        if name:
            known = {"名称": name, **known}
        targets = [key for key in targets_all if key not in filled]

        paths = []
        if crop_url:
            seed = self.image_dir / crop_url
            if seed.is_file():
                paths.append(seed)
        if category_id is not None:
            category = next(
                (item for item in repo.list_categories(self.db) if item["id"] == category_id),
                None,
            )
            for ref in (category or {}).get("refs", [])[-4:]:
                ref_path = self.reference_dir / ref["path"]
                if ref_path.is_file():
                    paths.append(ref_path)

        today = datetime.now().astimezone().date().isoformat()
        limit = int(self.config.get("ai.daily_limit") or 0)
        used = recognize.read_usage(self.db, today)
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

        with self._recognition_lock:
            self._recognition = {
                "categoryId": category_id,
                "step": "等待模型回复",
                "startedAt": time.time(),
            }
        try:
            result = recognize.call_suggest(self.recognizer, images, known, targets, deep)
        finally:
            with self._recognition_lock:
                self._recognition = None

        if not result["error"]:
            used = recognize.bump_usage(self.db, today)
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

    # ── 数据生命周期 ──
    def trim_observations(self):
        keep = int(self.config.get("data.observe_keep") or 0)
        return retention.trim(self.db, self.image_dir, keep)

    def reclaim_space(self):
        return retention.reclaim(self.db, self.image_dir, self.storage_low)

    def reclaim_orphans(self):
        return retention.reclaim_orphans(self.db, self.image_dir)

    def door_state(self):
        controller = getattr(self, "door", None)
        if controller is None:
            return "unknown"
        return controller.state()

    def door_level(self):
        """当前电平。接线时靠它确认有没有接反。"""
        controller = getattr(self, "door", None)
        if controller is None or not hasattr(controller, "level"):
            return None
        try:
            return controller.level()
        except Exception:
            return None

    def _rebuild_light(self):
        if self.light is not None:
            self.light.close()
            self.light = None
        try:
            light = load_light(
                self.config.get("trig.light_gpio"),
                self.config.get("trig.light_active_level") or "high",
            )
        except Exception as exc:
            logger.warning("柜内灯打开失败：%s", exc)
            light = None
        self.light = light or MockLight()
        self.sync_light()

    def sync_light(self):
        """门开着、或正在观察，灯就亮；两个原因都没了才灭。"""
        if self.light is None:
            return
        want = "on" if (self.is_observing() or self.door_state() == "open") else "off"
        if self.light_state() == want:
            return
        try:
            self.light.set(want == "on")
        except Exception as exc:
            logger.warning("柜内灯开关失败：%s", exc)
            return
        self.bus.publish("light.change", {"light": want})

    def light_state(self):
        if self.light is None:
            return "unknown"
        try:
            return self.light.state()
        except Exception:
            return "unknown"

    def set_light(self, on):
        if self.light is None:
            raise RuntimeError("没配柜内灯 GPIO")
        return self.light.set(on)

    def close(self):
        if self.detector is not None:
            self.detector.close()
        if self.embedder is not None:
            self.embedder.close()
        self.db.close()
