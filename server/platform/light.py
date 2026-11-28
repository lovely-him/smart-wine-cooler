"""柜内灯：只输出开关电平，灯的驱动与隔离在硬件侧。

两条自动规则叠在一起：**门开着**或**正在观察**，灯就亮；两个原因都没了才灭。
观察转圈的时候亮灯是为了补光，而观察流程本身要等画面稳定，所以不会把曝光搞坏。

线一旦 request 就由本进程持有，进程退出自动释放——不会留下"灯一直亮着没人管"。
"""

from .gpio import locate

ON = "on"
OFF = "off"


class LightSource:
    def set(self, on):
        raise NotImplementedError

    def state(self):
        raise NotImplementedError

    def close(self):
        pass


class MockLight(LightSource):
    """没有硬件时的灯：记住状态就好。"""

    def __init__(self):
        self._on = False

    def set(self, on):
        self._on = bool(on)
        return self.state()

    def state(self):
        return ON if self._on else OFF


class GpioLight(LightSource):
    """有效电平由 active_level 决定，默认高电平亮。"""

    def __init__(self, pin, active_level="high"):
        import gpiod

        self.pin = int(pin)
        self.active_level = str(active_level or "high").lower()
        path, offset = locate(self.pin)
        self._chip = gpiod.Chip(path)
        self._line = self._chip.get_line(offset)
        self._line.request(
            consumer="wine-cooler-light",
            type=gpiod.LINE_REQ_DIR_OUT,
            default_vals=[self._raw(False)],
        )

    def _raw(self, on):
        on = bool(on)
        if self.active_level == "high":
            return 1 if on else 0
        return 0 if on else 1

    def set(self, on):
        self._line.set_value(self._raw(on))
        return self.state()

    def state(self):
        return ON if self._line.get_value() == self._raw(True) else OFF

    def close(self):
        try:
            self.set(False)
            self._line.release()
            self._chip.close()
        except Exception:
            pass


def load_light(pin, active_level="high"):
    pin = str(pin or "").strip()
    if not pin:
        return None
    return GpioLight(pin, active_level)
