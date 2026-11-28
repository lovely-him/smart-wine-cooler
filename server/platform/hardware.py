"""柜内硬件：门磁与灯。

灯只有一个规则——门开着、或正在观察，灯就亮；两个原因都没了才灭。
"""

import logging

from server.platform.light import MockLight, load_light

logger = logging.getLogger(__name__)


class Hardware:
    def __init__(self, config, bus):
        self._config = config
        self._bus = bus
        self.door = None
        self.light = None
        self.rebuild_light(observing=False)

    def attach_door(self, door):
        self.door = door

    def door_state(self):
        if self.door is None:
            return "unknown"
        return self.door.state()

    def live_door(self):
        """界面只认开/关；「未知」不显示成一个看起来正常的状态。"""
        state = self.door_state()
        return None if state == "unknown" else state

    def door_level(self):
        """当前电平。接线时靠它确认有没有接反。"""
        if self.door is None or not hasattr(self.door, "level"):
            return None
        try:
            return self.door.level()
        except Exception:
            return None

    def poll_door(self, observing):
        """门磁边沿：先把灯对齐、把新状态推给界面，再把边沿交给调度器。"""
        if self.door is None:
            return None
        event = self.door.poll()
        if event is None:
            return None
        self.sync_light(observing)
        self._bus.publish(
            "door.change", {"door": self.live_door(), "doorLevel": self.door_level()}
        )
        return event

    def rebuild_light(self, observing):
        if self.light is not None:
            self.light.close()
            self.light = None
        try:
            light = load_light(
                self._config.get("trig.light_gpio"),
                self._config.get("trig.light_active_level") or "high",
            )
        except Exception as exc:
            logger.warning("柜内灯打开失败：%s", exc)
            light = None
        self.light = light or MockLight()
        self.sync_light(observing)

    def sync_light(self, observing):
        if self.light is None:
            return
        want = "on" if (observing or self.door_state() == "open") else "off"
        if self.light_state() == want:
            return
        try:
            self.light.set(want == "on")
        except Exception as exc:
            logger.warning("柜内灯开关失败：%s", exc)
            return
        self._bus.publish("light.change", {"light": want})

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

    def snapshot(self):
        return {
            "door": self.live_door(),
            "doorLevel": self.door_level(),
            "light": self.light_state(),
        }
