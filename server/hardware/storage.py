"""存储：容量探测与目录准备。

满了不删任何东西，只把状态报出去——清理要么由人做，要么由人主动设定的保留策略做。
"""

import shutil
from pathlib import Path


def usage(path):
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(target)
    return {"total": total, "used": used, "free": free}


def ensure_dir(*paths):
    for item in paths:
        Path(item).mkdir(parents=True, exist_ok=True)
