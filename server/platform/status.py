"""界面要的整机状态：存储剩多少、门与灯什么状态、观察跑没跑。

每次现查，不缓存、不落盘。摄像头能不能开由 `vision` 自己探测，这里只取结果。
"""

import socket
import time
from datetime import datetime


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_ip():
    """本机在这个局域网里的地址。UDP connect 只查路由，不发包。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def host_uptime():
    """设备开机时长。服务重启不会让它归零，用它区分「设备重启」与「服务重启」。"""
    try:
        with open("/proc/uptime", encoding="ascii") as fp:
            return int(float(fp.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def live_status(camera_status, housekeeping, hardware, observe, started_at):
    usage = housekeeping.usage()
    return {
        "cameras": camera_status(),
        "storageFreeBytes": usage["free"],
        "storageTotalBytes": usage["total"],
        **observe.snapshot(),
        **hardware.snapshot(),
        "now": now(),
        "serviceUptimeSeconds": int(time.time() - started_at),
        "hostUptimeSeconds": host_uptime(),
        "ip": local_ip(),
        "storageLow": housekeeping.storage_low(),
    }
