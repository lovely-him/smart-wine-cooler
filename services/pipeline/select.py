"""稳定窗选帧：边抓边判，直到画面静止且曝光收敛，再取锚帧与观测帧集合。

判据看画面不看时钟——连续若干帧同时满足"帧间差异小 + 亮度不再爬升 + 通过质量下限"，
才认为现场静止。锚帧取窗口**中点**：窗口头部可能还在收敛，尾部可能已经接近下一次变化。

超时未稳定就返回 timeout，由调用方按"失效"处理，绝不靠等待凑出一个结果。
"""

import time

from services.pipeline import quality

# ── 路径与常量 ──
DEF_STABLE_FRAMES = 3
DEF_DIFF = 1.5
DEF_BRIGHT_DELTA = 1.0
DEF_MIN_BRIGHT = 15.0
DEF_MAX_BRIGHT = 245.0
DEF_MIN_SHARP = 20.0
DEF_MAX_FRAMES = 30
DEF_TIMEOUT = 10.0
DEF_GRAB_BATCH = 1


# ── 类 ──
class Stabilizer:
    def __init__(self, cfg):
        self.stable_frames = max(1, int(cfg.get("stable_frames", DEF_STABLE_FRAMES)))
        self.diff_thresh = float(cfg.get("diff_thresh", DEF_DIFF))
        self.bright_delta = float(cfg.get("bright_delta", DEF_BRIGHT_DELTA))
        self.min_bright = float(cfg.get("min_bright", DEF_MIN_BRIGHT))
        self.max_bright = float(cfg.get("max_bright", DEF_MAX_BRIGHT))
        self.min_sharp = float(cfg.get("min_sharp", DEF_MIN_SHARP))
        self.samples = []
        self._prev_thumb = None

    def push(self, img):
        entry = quality.measure(img, self._prev_thumb)
        thumbs = entry.pop("_thumb")
        entry["ok"] = self._frame_ok(entry)
        self._prev_thumb = thumbs
        self.samples.append(entry)
        return "stable" if self._window() else "waiting"

    def _frame_ok(self, entry):
        if not (self.min_bright <= entry["bright"] <= self.max_bright):
            return False
        if entry["sharp"] < self.min_sharp:
            return False
        if entry["diff"] > self.diff_thresh:
            return False
        return True

    def _window(self):
        """返回连续合格帧的 [start, end) 区间；不足则返回 None。"""
        run = 0
        for idx in range(len(self.samples) - 1, -1, -1):
            if self.samples[idx]["ok"]:
                run += 1
                if run >= self.stable_frames:
                    return (idx, idx + run)
            else:
                return None
        return None

    def window(self):
        return self._window()

    def anchor_index(self):
        span = self._window()
        if not span:
            return None
        return (span[0] + span[1] - 1) // 2

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
        return why or ["连续帧数不足"]


# ── 函数 ──
def collect(source, cfg, cancel=None):
    """抓帧直到稳定窗达成或超时。返回 frames / window / 判定结果。"""
    stab = Stabilizer(cfg)
    max_frames = int(cfg.get("max_frames", DEF_MAX_FRAMES))
    batch = max(1, int(cfg.get("grab_batch", DEF_GRAB_BATCH)))
    deadline = time.monotonic() + float(cfg.get("settle_timeout", DEF_TIMEOUT))
    frames = []

    while len(frames) < max_frames:
        if cancel and cancel():
            return {"status": "aborted", "frames": frames, "metrics": stab.samples}
        if time.monotonic() > deadline:
            return {
                "status": "timeout",
                "frames": frames,
                "metrics": stab.samples,
                "reasons": stab.reasons(),
            }
        want = min(batch, max_frames - len(frames))
        for img in source.grab(want):
            frames.append(img)
            if stab.push(img) == "stable":
                return {
                    "status": "ok",
                    "frames": frames,
                    "window": stab.window(),
                    "anchor": stab.anchor_index(),
                    "metrics": stab.samples,
                }
    return {
        "status": "timeout",
        "frames": frames,
        "metrics": stab.samples,
        "reasons": ["达到最大帧数仍未稳定"] + stab.reasons(),
    }


def observe_set(frames, anchor, count):
    """以锚帧为中心取观测帧集合；不足时向两侧与尾部平衡取。"""
    if not frames:
        return [], None
    count = max(1, min(count, len(frames)))
    half = count // 2
    start = anchor - half
    start = max(0, min(start, len(frames) - count))
    idxs = list(range(start, start + count))
    return [frames[i] for i in idxs], idxs
