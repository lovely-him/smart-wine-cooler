"""GPIO 定位：把配置里填的「全局引脚号」翻译成「哪块芯片的第几根线」。

配置里填的是 sysfs 那套全局编号（`/sys/class/gpio/gpio141` 就是 141），但字符设备
`/dev/gpiochipN` 的 N 是内核按枚举顺序起的，和 sysfs 里按 base 起的目录名
（`gpiochip128`）对不上——板上 141 号脚落在 `/sys/class/gpio/gpiochip128` 里，
字符设备却叫 `/dev/gpiochip4`。所以这里靠"同一个 GPIO 控制器"这个拓扑关系对上：
sysfs 类目录是 `<控制器>/gpio/<目录>`，字符设备是 `<控制器>/gpiochipN`。
"""

import os
from pathlib import Path

GPIO_ROOT = Path("/sys/class/gpio")
GPIO_BUS = Path("/sys/bus/gpio/devices")


def _device_name(class_dir):
    device = Path(os.path.realpath(class_dir)).parent.parent
    for dev in GPIO_BUS.iterdir():
        if Path(os.path.realpath(dev)).parent == device:
            return dev.name
    raise ValueError(f"{class_dir} 找不到对应的字符设备")


def locate(number):
    """返回 (字符设备路径, 行偏移)。"""
    number = int(number)
    for entry in GPIO_ROOT.glob("gpiochip*"):
        try:
            base = int((entry / "base").read_text(encoding="utf-8"))
            count = int((entry / "ngpio").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if base <= number < base + count:
            return f"/dev/{_device_name(entry)}", number - base
    raise ValueError(f"GPIO {number} 不属于任何一块 gpiochip")

