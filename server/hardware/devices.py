"""枚举板上的 USB 摄像头，给设置页当候选。

只认 by-id 里的 `-video-index0`：
- `/dev/videoNN` 会随插拔和重启变号，存下来下次就指错设备；
- 同一个摄像头的 `-video-index1` 是元数据节点，能打开但永远不出帧；
- `/dev/videoNN` 里还有一大批芯片内部节点（rkisp / hdmirx / rkcif），选错等于没画面。
"""

import os
from pathlib import Path

BY_ID = Path("/dev/v4l/by-id")
SUFFIX = "-video-index0"


def label_of(path):
    name = Path(path).name
    if name.startswith("usb-"):
        name = name[len("usb-") :]
    if name.endswith(SUFFIX):
        name = name[: -len(SUFFIX)]
    return name.replace("_", " ")


def list_cameras(root=None):
    root = Path(root or BY_ID)
    if not root.is_dir():
        return []
    found = []
    for entry in sorted(root.glob(f"*{SUFFIX}")):
        path = str(entry)
        found.append({"path": path, "label": label_of(path), "online": os.path.exists(path)})
    return sorted(found, key=lambda item: item["label"])
