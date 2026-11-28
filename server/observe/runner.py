"""一次观察的编排与状态：同一时刻只跑一次，门关的重放由这里记。

跑在独立线程里——一次观察要好几秒，占着事件循环界面就僵住。
"""

import logging
import threading
from datetime import datetime

from server.platform.db import observations
from server.observe.scan import run_scan

logger = logging.getLogger(__name__)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ObserveRunner:
    def __init__(
        self,
        db,
        image_dir,
        plan,
        engines,
        housekeeping,
        bus,
        sync_light,
        interrupted,
    ):
        self._db = db
        self._image_dir = image_dir
        self._plan = plan
        self._engines = engines
        self._housekeeping = housekeeping
        self._bus = bus
        self._sync_light = sync_light
        self._interrupted = interrupted
        self._lock = threading.Lock()
        self._observing = False
        self._pending_trigger = None
        self._scan_started_at = None
        self._scan_trigger = None
        latest = observations.latest_observation(db)
        self._last_observation_id = latest["id"] if latest else None

    def is_observing(self):
        return self._observing

    def snapshot(self):
        return {
            "observing": self._observing,
            "scanStartedAt": self._scan_started_at,
            "scanTrigger": self._scan_trigger,
            "lastObservationId": self._last_observation_id,
        }

    def start_observe(self, trigger="manual"):
        """在后台线程跑一次观察，立即返回是否受理。"""
        with self._lock:
            if self._observing:
                # 门关是权威边沿：柜内刚被动过，正在跑的那次已经作废，收尾后得补跑一次。
                if trigger == "door":
                    self._pending_trigger = trigger
                return False
            self._observing = True
        self._sync_light(True)
        threading.Thread(
            target=self._worker, args=(trigger,), daemon=True, name="observe"
        ).start()
        return True

    def _worker(self, trigger):
        try:
            self._run(trigger)
        except Exception:
            logger.exception("观察失败")
        finally:
            self._db.drop_thread_conn()

    def _run(self, trigger):
        started = _now()
        self._scan_started_at = started
        self._scan_trigger = trigger
        obs_id = None
        status = "void"
        self._bus.publish(
            "observe.start",
            {"observing": True, "scanStartedAt": started, "scanTrigger": trigger},
        )
        try:
            obs_id, status = run_scan(
                db=self._db,
                image_dir=self._image_dir,
                plan=self._plan,
                engines=self._engines,
                housekeeping=self._housekeeping,
                trigger=trigger,
                cancel=self._interrupted.is_set,
            )
            self._last_observation_id = obs_id
            self._housekeeping.trim()
            return {"observation_id": obs_id, "status": status}
        finally:
            self._scan_started_at = None
            self._scan_trigger = None
            with self._lock:
                self._observing = False
                pending = self._pending_trigger
                self._pending_trigger = None
            self._sync_light(False)
            self._bus.publish(
                "observe.done", {"observationId": obs_id, "status": status}
            )
            if pending:
                self.start_observe(pending)
