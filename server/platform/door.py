"""门磁 / 按键：真实 GPIO 与模拟实现，接口一致。

语义只有两条：
- **关上**：柜内状态此刻稳定，该看一眼了
- **打开**：柜内正在被人改动，进行中的观察要作废

按键与门磁共用这一路输入——按键只是手动产生一次「关上」。
"""

from .gpio import locate

CLOSED = "closed"
OPENED = "opened"


class DoorSource:
    def poll(self):
        """返回自上次调用以来的边沿事件，没有则 None。"""
        raise NotImplementedError

    def state(self):
        """当前是"open" / "closed" / "unknown"。"""
        raise NotImplementedError

    def close(self):
        pass


class MockDoor(DoorSource):
    """模拟门磁：由外部调用 set() 制造边沿，用于没有硬件的环境。"""

    def __init__(self, initial=CLOSED):
        self._state = initial
        self._pending = []

    def set(self, state):
        if state == self._state:
            return
        self._state = state
        self._pending.append(CLOSED if state == CLOSED else OPENED)

    def toggle(self):
        self.set(OPENED if self._state == CLOSED else CLOSED)

    def poll(self):
        return self._pending.pop(0) if self._pending else None

    def state(self):
        return self._state

    def describe(self):
        return f"mock door（当前 {self._state}）"


class GpioDoor(DoorSource):
    """用 libgpiod 读一根线。

    只在电平**变化**时产生事件，所以第一次 poll 不会误报一次关门。
    输入开了内部上拉：常闭门磁接 GND 时关门读低、开门读高，断线也读高（"门开着"）。
    外部再加 10k 上拉更稳，但不是必须的。

    走字符设备而不是 /sys/class/gpio：那套接口已经废弃，而且设不了上下拉。
    """

    def __init__(self, cfg):
        import gpiod

        self.pin = int(cfg["pin"])
        self.closed_level = str(cfg.get("closed_level") or "low").lower()
        path, offset = locate(self.pin)
        self._chip = gpiod.Chip(path)
        self._line = self._chip.get_line(offset)
        self._line.request(
            consumer="wine-cooler-door",
            type=gpiod.LINE_REQ_DIR_IN,
            flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
        )
        self._last = None

    def level(self):
        return "high" if self._line.get_value() == 1 else "low"

    def _read(self):
        high = self._line.get_value() == 1
        closed = (not high) if self.closed_level == "low" else high
        return CLOSED if closed else OPENED

    def poll(self):
        current = self._read()
        previous, self._last = self._last, current
        if previous is None or previous == current:
            return None
        return current

    def state(self):
        current = self._read() if self._last is None else self._last
        self._last = current
        return "closed" if current == CLOSED else "open"

    def close(self):
        try:
            self._line.release()
            self._chip.close()
        except Exception:
            pass


def load_door(cfg):
    backend = cfg.get("backend", "mock")
    if backend == "gpio":
        return GpioDoor(cfg)
    if backend == "mock":
        return MockDoor(cfg.get("initial", CLOSED))
    raise ValueError(f"未知门磁后端: {backend}")


def main():
    door = MockDoor()
    print(f"模拟门磁：{door.describe()}")
    for step in (OPENED, CLOSED, OPENED, CLOSED):
        door.set(step)
        event = door.poll()
        print(f"  设为 {step:<6} → 事件 {event}  当前 {door.state()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
