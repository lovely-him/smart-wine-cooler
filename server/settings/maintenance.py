"""维护动作：重启服务、重启设备、导出、清理。

前两个必须先给响应再动作——进程一退出，请求就没了。
清理只按人设定的保留策略删，绝不自作主张（`09` 第 8 节）。
"""

import os
import sqlite3
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from server.platform.settings_def import SETTINGS


def restart_service(delay=1.0):
    """unit 里配的是 Restart=on-failure，所以非零退出就等于让 systemd 把它拉起来。"""

    def _leave():
        time.sleep(delay)
        os._exit(3)

    threading.Thread(target=_leave, daemon=True).start()
    return "服务正在重启"


def restart_device():
    result = subprocess.run(
        ["sudo", "-n", "systemctl", "reboot"], capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "重启设备失败")
    return "设备正在重启"


def _drop_sensitive(snapshot):
    """导出包要能随便传，敏感项连行都不留。"""
    keys = [(item.key,) for item in SETTINGS if item.sensitive]
    conn = sqlite3.connect(snapshot)
    try:
        conn.executemany("DELETE FROM setting WHERE key = ?", keys)
        conn.commit()
    finally:
        conn.close()


def export(config, db, image_dir, reference_dir):
    target = str(config.get("data.export_target") or "").strip()
    if not target:
        raise ValueError("先填 DATA-04 导出目标地址")

    destination = Path(target)
    if destination.is_dir():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = destination / f"wine-cooler-{stamp}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)

    snapshot = destination.with_suffix(".db")
    db.write(lambda conn: conn.execute("VACUUM INTO ?", (str(snapshot),)))
    try:
        _drop_sensitive(snapshot)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "app.db")
            for folder, name in ((image_dir, "images"), (reference_dir, "refs")):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        archive.write(path, f"{name}/{path.relative_to(folder)}")
    finally:
        snapshot.unlink(missing_ok=True)
    return f"已导出到 {destination}"


def run(action, *, config, db, image_dir, reference_dir):
    if action == "restart-service":
        return restart_service()
    if action == "restart-device":
        return restart_device()
    if action == "export":
        return export(
            config=config, db=db, image_dir=image_dir, reference_dir=reference_dir
        )
    raise ValueError(f"不认识的动作：{action}")
