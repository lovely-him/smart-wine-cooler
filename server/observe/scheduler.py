"""触发与准入：三条路径发起的是同一种东西——一次观察。

门磁与按键共用一路输入：**关上**表示柜内此刻稳定，**打开**表示正在被改动。
定时只在「距上次观察已超过设定间隔」时才补，避免门磁已经扫过又被重复扫。
"""

import threading
from datetime import datetime

from server.platform.db import observations
from server.platform.door import CLOSED, OPENED

POLL_INTERVAL = 0.2


class Scheduler(threading.Thread):
    def __init__(self, config, db, observe, hardware, interrupted):
        super().__init__(daemon=True, name="scheduler")
        self._config = config
        self._db = db
        self._observe = observe
        self._hardware = hardware
        self._interrupted = interrupted
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _timer_due(self):
        if not self._config.get("trig.timer_enabled"):
            return False
        minutes = float(self._config.get("trig.timer_minutes") or 0)
        if minutes <= 0:
            return False
        latest = observations.latest_observation(self._db)
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
        event = self._hardware.poll_door(self._observe.is_observing())
        if event == OPENED:
            self._interrupted.set()
        elif event == CLOSED:
            self._interrupted.clear()
            self._observe.start_observe("door")

        if not self._observe.is_observing() and self._timer_due():
            if blocked_reason(self._hardware) is None:
                self._observe.start_observe("timer")


def manual_allowed(config):
    return bool(config.get("trig.manual_enabled"))


def blocked_reason(hardware):
    """现在为什么不能开始一次观察；能开始返回 None。"""
    if hardware.door_state() == "open":
        return "门打开着，等关上再观察"
    return None
