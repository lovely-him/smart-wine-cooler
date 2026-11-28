"""V4L2 采集：一次开流抓一包，再按 JPEG 起止标记切开。

一次开流读多帧比"每帧起一次进程"省得多。`skip` 丢掉开流后的头几帧——UVC 刚
开流那几张往往是全黑或曝光未收敛的。

`device` 必须写 `/dev/v4l/by-id/...-video-index0`：USB 摄像头节点号会随重启/插拔
变化，写死 `/dev/videoNN` 在重启后很可能指到 metadata 节点——能打开却永远不出帧，
现象是开流一直不返回。
"""

import os
import re
import subprocess
import tempfile
from io import BytesIO

from PIL import Image

CHECK_TIMEOUT = 5
GRAB_TIMEOUT = 5
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def list_formats(device):
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", device, "--list-formats"],
            capture_output=True,
            timeout=CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{device} 在 {CHECK_TIMEOUT}s 内未响应（设备挂起？）") from None
    text = (result.stdout + result.stderr).decode("utf-8", "ignore")
    if result.returncode != 0:
        raise RuntimeError(f"打不开摄像头 {device}: {text.strip()[:160]}")
    return re.findall(r"'(\w{4})'", text)


def split_jpeg(data):
    frames, pos = [], 0
    while True:
        start = data.find(SOI, pos)
        if start < 0:
            break
        end = data.find(EOI, start + 2)
        if end < 0:
            break
        frames.append(data[start : end + 2])
        pos = end + 2
    return frames


class V4l2Camera:
    def __init__(self, cfg):
        self.device = cfg["device"]
        self.width = int(cfg["width"])
        self.height = int(cfg["height"])
        self.skip = int(cfg["skip"])
        self.batch = int(cfg["batch"])

        if not os.path.exists(self.device):
            raise RuntimeError(f"摄像头不存在: {self.device}")
        formats = list_formats(self.device)
        if "MJPG" not in formats:
            shown = "/".join(formats) or "无"
            raise RuntimeError(
                f"{self.device} 不能出 MJPG（可用格式: {shown}）。"
                "若这是 USB 摄像头的 metadata 节点，改用 *-video-index0"
            )

    def grab(self, count=None):
        """抓 count 帧（默认一包），返回 PIL RGB 图列表。"""
        count = int(count or self.batch)
        with tempfile.TemporaryDirectory() as tmp:
            dump = os.path.join(tmp, "frames.mjpg")
            cmd = [
                "v4l2-ctl",
                "-d", self.device,
                "--set-fmt-video", f"width={self.width},height={self.height},pixelformat=MJPG",
                "--stream-mmap",
                "--stream-count", str(count),
                "--stream-skip", str(self.skip),
                "--stream-to", dump,
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"摄像头 {self.device} 在 {GRAB_TIMEOUT}s 内没有出帧"
                    "（设备挂起，或指向了 metadata 节点）"
                ) from None
            if result.returncode != 0 or not os.path.exists(dump):
                raise RuntimeError(
                    f"v4l2-ctl 采集失败: {result.stderr.decode('utf-8', 'ignore').strip()}"
                )
            with open(dump, "rb") as fp:
                raw = fp.read()

        chunks = split_jpeg(raw)
        if not chunks:
            raise RuntimeError("采集结果里没有完整 JPEG 帧")
        return [Image.open(BytesIO(chunk)).convert("RGB") for chunk in chunks]

    def describe(self):
        return f"v4l2 {self.device} {self.width}x{self.height} skip={self.skip}"
