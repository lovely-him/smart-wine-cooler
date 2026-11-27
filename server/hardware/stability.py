"""抓帧直到画面静止，然后只交出一帧。

判据看画面不看时钟：连续若干帧同时满足「帧间差异小 + 亮度落在可用区间 + 够清晰」，
才认为现场已经静止。够了就取窗口**中点**那一帧——窗口头部可能还在曝光收敛，
尾部可能已经接近下一次变化。

超时未静止就判失败，绝不靠多等一会儿凑出一个结果。有没有超时只看
deadline（`settle_timeout`），不看抓了多少帧：帧数和秒的换算取决于帧率，
同一个数字在不同摄像头上完全不是一回事。
"""

import time
from collections import deque

from . import lens, quality

DEFAULTS = {
    "stable_frames": 3,
    "diff_thresh": 1.5,
    "bright_delta": 1.0,
    "min_bright": 15.0,
    "max_bright": 245.0,
    "min_sharp": 20.0,
    "settle_timeout": 10.0,
    "grab_batch": 10,
    "rotate": 0,
    "fov": 0.0,
    "k1": 0.0,
    "k2": 0.0,
    "k3": 0.0,
    "k4": 0.0,
}


class Stabilizer:
    def __init__(self, cfg):
        cfg = {**DEFAULTS, **cfg}
        self.stable_frames = max(1, int(cfg["stable_frames"]))
        self.diff_thresh = float(cfg["diff_thresh"])
        self.min_bright = float(cfg["min_bright"])
        self.max_bright = float(cfg["max_bright"])
        self.min_sharp = float(cfg["min_sharp"])
        self.samples = []
        self._prev_thumb = None

    def push(self, img):
        entry = quality.measure(img, self._prev_thumb)
        self._prev_thumb = entry.pop("thumb")
        entry["ok"] = self._frame_ok(entry)
        self.samples.append(entry)
        return "stable" if self._window() else "waiting"

    def _frame_ok(self, entry):
        if not (self.min_bright <= entry["bright"] <= self.max_bright):
            return False
        if entry["sharp"] < self.min_sharp:
            return False
        return entry["diff"] <= self.diff_thresh

    def _window(self):
        run = 0
        for idx in range(len(self.samples) - 1, -1, -1):
            if not self.samples[idx]["ok"]:
                return None
            run += 1
            if run >= self.stable_frames:
                return (idx, idx + run)
        return None

    def reasons(self):
        if not self.samples:
            return ["没有帧"]
        last = self.samples[-1]
        why = []
        if not (self.min_bright <= last["bright"] <= self.max_bright):
            why.append(f"亮度 {last['bright']:.1f} 越界")
        if last["sharp"] < self.min_sharp:
            why.append(f"清晰度 {last['sharp']:.1f} 过低")
        if last["diff"] > self.diff_thresh:
            why.append(f"帧间差异 {last['diff']:.2f} 偏大")
        return why or ["连续合格帧数不足"]


def collect(camera, cfg):
    """抓帧到稳定窗达成或超时。

    返回 status(ok/timeout)；达标时带 anchor（那一帧）与已抓帧数，失败时带最后一帧与 reasons。

    只留最近 `stable_frames` 帧：稳定窗一旦达成，中点就在这几帧里。全留着的话，
    10 秒的限额在高帧率摄像头上能攒下上千张图，整台设备会被吃光内存。

    几何纠正（去畸变、旋转）只对交出去的那一帧做：它是确定的变换，
    不改变「画面稳不稳」的判断，没必要每帧都算。
    """
    cfg = {**DEFAULTS, **cfg}
    stab = Stabilizer(cfg)
    batch = max(1, int(cfg["grab_batch"]))
    deadline = time.monotonic() + float(cfg["settle_timeout"])
    recent = deque(maxlen=stab.stable_frames)
    grabbed = 0

    while time.monotonic() <= deadline:
        for img in camera.grab(batch):
            grabbed += 1
            recent.append(img)
            if stab.push(img) == "stable":
                return {
                    "status": "ok",
                    "frames": grabbed,
                    "anchor": lens.fix(recent[(stab.stable_frames - 1) // 2], cfg),
                    "metrics": stab.samples,
                }

    return {
        "status": "timeout",
        "frames": grabbed,
        "last": lens.fix(recent[-1], cfg) if recent else None,
        "reasons": ["等到时间上限仍未静止"] + stab.reasons(),
    }


def check_usable(img, cfg):
    """单张画面能否直接用。返回 (是否可用, 原因)。"""
    cfg = {**DEFAULTS, **cfg}
    entry = quality.measure(img)
    if not (cfg["min_bright"] <= entry["bright"] <= cfg["max_bright"]):
        return False, f"亮度 {entry['bright']:.1f} 越界"
    if entry["sharp"] < cfg["min_sharp"]:
        return False, f"清晰度 {entry['sharp']:.1f} 过低"
    return True, ""
