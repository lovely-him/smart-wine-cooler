"""触发与准入：三条路径发起的是同一种东西——一次观察。

门磁与按键共用一路输入：**关上**表示柜内此刻稳定，**打开**表示正在被改动。
定时只在「距上次观察已超过设定间隔」时才补，避免门磁已经扫过又被重复扫。
"""

import threading
import time
from datetime import datetime

from server.db import repo
from server.hardware.door import CLOSED, OPENED

POLL_INTERVAL = 0.2


class Scheduler(threading.Thread):
    def __init__(self, runtime, door=None):
        super().__init__(daemon=True, name="scheduler")
        self.runtime = runtime
        self.door = door
        self._stop = threading.Event()
        self._cancel = threading.Event()

    def stop(self):
        self._stop.set()

    def cancelled(self):
        return self._cancel.is_set()

    def clear_cancel(self):
        self._cancel.clear()

    def _timer_due(self):
        if not self.runtime.config.get("trig.timer_enabled"):
            return False
        minutes = float(self.runtime.config.get("trig.timer_minutes") or 0)
        if minutes <= 0:
            return False
        latest = repo.latest_observation(self.runtime.db)
        if latest is None:
            return True
        try:
            last = datetime.fromisoformat(latest["started_at"])
        except ValueError:
            return True
        return (datetime.now().astimezone() - last).total_seconds() >= minutes * 60

    def run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(POLL_INTERVAL)

    def _tick(self):
        if self.door is not None:
            event = self.door.poll()
            if event is not None:
                self.runtime.sync_light()
                self.runtime.bus.publish(
                    "door.change",
                    {
                        "door": self.runtime.live_door(),
                        "doorLevel": self.runtime.door_level(),
                    },
                )
            if event == OPENED:
                self._cancel.set()
            elif event == CLOSED:
                self._cancel.clear()
                self.runtime.start_observe("door")

        if not self.runtime.is_observing() and self._timer_due():
            if blocked_reason(self.runtime) is None:
                self.runtime.start_observe("timer")


def manual_allowed(runtime):
    return bool(runtime.config.get("trig.manual_enabled"))


def blocked_reason(runtime):
    """现在为什么不能开始一次观察；能开始返回 None。"""
    if runtime.door_state() == "open":
        return "门打开着，等关上再观察"
    return None


def wait_idle(runtime, timeout=3600.0):
    deadline = time.monotonic() + timeout
    while runtime.is_observing():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True
