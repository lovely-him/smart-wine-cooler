"""流水线与队列：每个摄像头一条状态机，全局只有一个执行槽（串行），**一次盘点 = 整柜一次扫描**。

要点全部来自 docs/inventory-loop.md §3：
- 触发单位是柜：`start_scan()` 把所有层依次入队，全部完成才算一次扫描
- 排队在**采集之前**，轮到它时从连拍整个重来（所以"排队后重新判定稳定窗"是自然结果）
- 运行中再次扫描 = **重启**：代次号 +1，旧任务的任何结果都不得提交
- 中断（停止/门开）= 代次号 +1 并立即结束，不产生观测，整次扫描作废
- 任一层没拿到有效观测 → 整次扫描作废，当前状态保持上一次完整扫描
"""

import queue
import threading
import time

from services.capture import load_source
from services.classify import features as cls_features
from services.classify.cluster import DEF_THRESHOLD, cluster_vectors
from services.pipeline import consistency, select
from services.store.repo import (
    ORDER_LEFT_RIGHT,
    SCAN_ABORTED,
    SCAN_DONE,
    SCAN_FAILED,
    SCAN_RUNNING,
    UNKNOWN_ID,
    counts_of,
    order_of,
    sort_detections,
)

# ── 路径与常量 ──
STATE_IDLE = "idle"
STATE_QUEUED = "queued"
STATE_SETTLING = "settling"
STATE_MEASURING = "measuring"
STATE_DONE = "done"
STATE_ABORTED = "aborted"
STATE_ERROR = "error"
DEF_OBSERVE_FRAMES = 3
TRACE_LIMIT = 200
SCAN_TIMEOUT_PER_LAYER = 30


# ── 类 ──
class CameraRunner:
    def __init__(self, cam, pipeline_cfg, infer_cfg, detector):
        self.id = cam["id"]
        self.name = cam.get("name", cam["id"])
        self.cfg = pipeline_cfg
        self.infer_cfg = infer_cfg
        self.source = load_source(cam["capture"])
        self.order_by = (cam.get("capture") or {}).get("order", ORDER_LEFT_RIGHT)
        self.detector = detector
        self.lock = threading.Lock()
        self.state = STATE_IDLE
        self.epoch = 0
        self.observation = None
        self.error = ""

    def owns(self, epoch):
        return self.epoch == epoch

    def snapshot(self):
        with self.lock:
            return {
                "id": self.id,
                "name": self.name,
                "state": self.state,
                "epoch": self.epoch,
                "source": self.source.describe(),
                "observation": self.observation,
                "error": self.error,
            }


class PipelineService:
    def __init__(self, cfg, detector, store=None, classifier=None):
        self.pipeline_cfg = cfg.get("pipeline") or {}
        self.store = store
        self.classifier = classifier
        self.runners = {}
        for cam in cfg["cameras"]:
            self.runners[cam["id"]] = CameraRunner(cam, self.pipeline_cfg, cfg["infer"], detector)

        self.trace = []
        self.trace_lock = threading.Lock()
        self.jobs = queue.Queue()
        self.scan = None
        self.scan_lock = threading.RLock()
        self.scan_token = 0
        self.worker = threading.Thread(target=self._loop, daemon=True, name="pipeline")
        self.worker.start()

    # ── 对外接口 ──
    def start_scan(self):
        """整柜盘点：所有层依次跑完才算一次扫描；重复开始时先作废前一次。"""
        with self.scan_lock:
            if self.scan and self.scan["state"] == SCAN_RUNNING:
                self.abort_scan()
            self.scan_token += 1
            token = self.scan_token
            scan_id = self.store.open_scan() if self.store else 0
            epochs = {camera_id: self._trigger(camera_id) for camera_id in self.runners}
            self.scan = {
                "id": scan_id,
                "token": token,
                "state": SCAN_RUNNING,
                "epochs": epochs,
                "started_at": time.monotonic(),
            }
        self._note("scan", SCAN_RUNNING, token)
        threading.Thread(
            target=self._settle_scan, args=(scan_id, token, epochs), daemon=True, name="scan"
        ).start()
        return {"scan_id": scan_id, "token": token, "epochs": epochs}

    def abort_scan(self):
        """作废本次扫描：所有层代次号 +1，排队中的任务自动跳过。"""
        with self.scan_lock:
            scan = self.scan
            if not scan or scan["state"] != SCAN_RUNNING:
                raise ValueError("没有正在进行的盘点")
            self.scan_token += 1
            for camera_id in self.runners:
                self._abort(camera_id)
            self.scan = {**scan, "state": SCAN_ABORTED}
            if self.store:
                self.store.close_scan(scan["id"], SCAN_ABORTED, "用户停止")
            scan_id = scan["id"]
        self._note("scan", SCAN_ABORTED, self.scan_token)
        return {"scan_id": scan_id, "state": SCAN_ABORTED}

    def scan_snapshot(self):
        with self.scan_lock:
            if not self.scan:
                return {"id": 0, "state": "idle", "finished": {}, "note": ""}
            finished = {}
            for camera_id, epoch in self.scan["epochs"].items():
                runner = self.runners[camera_id]
                with runner.lock:
                    finished[camera_id] = (
                        runner.state if runner.epoch == epoch else "stale"
                    )
            return {
                "id": self.scan["id"],
                "state": self.scan["state"],
                "finished": finished,
                "note": self.scan.get("note", ""),
            }

    def status(self):
        return [runner.snapshot() for runner in self.runners.values()]

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            unfinished = self.jobs.unfinished_tasks
            if unfinished == 0:
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.02)

    def shutdown(self):
        self.jobs.put(None)
        self.worker.join(timeout=5)

    # ── 内部 ──
    def _trigger(self, camera_id):
        runner = self._runner(camera_id)
        with runner.lock:
            runner.epoch += 1
            runner.state = STATE_QUEUED
            runner.error = ""
            epoch = runner.epoch
        self._note(camera_id, STATE_QUEUED, epoch)
        self.jobs.put((camera_id, epoch))
        return epoch

    def _abort(self, camera_id):
        runner = self._runner(camera_id)
        with runner.lock:
            runner.epoch += 1
            runner.state = STATE_ABORTED
            epoch = runner.epoch
        self._note(camera_id, STATE_ABORTED, epoch)
        return epoch

    def _settle_scan(self, scan_id, token, epochs):
        """等所有层跑完，再判定这次扫描是完成还是作废。"""
        timeout = SCAN_TIMEOUT_PER_LAYER * max(1, len(self.runners))
        self.wait(timeout)
        with self.scan_lock:
            if token != self.scan_token:
                return
            previous = self._reported_of_last_done()
            bad = []
            for camera_id, epoch in epochs.items():
                runner = self.runners[camera_id]
                with runner.lock:
                    done = runner.epoch == epoch and runner.state == STATE_DONE
                    observation = runner.observation
                if not done:
                    bad.append(f"{camera_id} 未完成（{runner.state}）")
                elif not observation or observation.get("tier") == consistency.TIER_INVALID:
                    bad.append(f"{camera_id} 未取得有效观测")
            state = SCAN_FAILED if bad else SCAN_DONE
            note = "；".join(bad)
            self.scan = {**self.scan, "state": state, "note": note}
            if self.store:
                self.store.close_scan(scan_id, state, note)
                if state == SCAN_DONE:
                    current = {
                        obs["camera"]: counts_of(obs["detections"])
                        for obs in self.store.observations_of_scan(scan_id)
                    }
                    if current != previous:
                        self.store.add_event(
                            scan_id, (self.store.latest_baseline() or {}).get("id")
                        )
        self._note("scan", state, token)

    def _reported_of_last_done(self):
        if self.store is None:
            return {}
        scan = self.store.latest_scan(SCAN_DONE)
        if not scan:
            return {}
        return {
            obs["camera"]: counts_of(obs["detections"])
            for obs in self.store.observations_of_scan(scan["id"])
        }

    def _runner(self, camera_id):
        if camera_id not in self.runners:
            names = ", ".join(self.runners)
            raise ValueError(f"没有这个摄像头: {camera_id}（可选: {names}）")
        return self.runners[camera_id]

    def _note(self, camera_id, state, epoch):
        with self.trace_lock:
            self.trace.append((round(time.monotonic(), 4), camera_id, state, epoch))
            if len(self.trace) > TRACE_LIMIT:
                del self.trace[: -TRACE_LIMIT]

    def _set_state(self, runner, state, epoch):
        with runner.lock:
            if runner.epoch != epoch:
                return False
            runner.state = state
        self._note(runner.id, state, epoch)
        return True

    def _commit(self, runner, epoch, observation, anchor_img=None, crops=None):
        with runner.lock:
            if runner.epoch != epoch:
                return False
            runner.observation = observation
            runner.state = STATE_DONE
        self._note(runner.id, STATE_DONE, epoch)
        self._persist(runner, observation, anchor_img, crops)
        return True

    def _persist(self, runner, observation, anchor_img, crops=None):
        """落库。事件不在这里记——一次扫描有没有变化，要等整柜扫完才知道。"""
        if self.store is None:
            return
        try:
            baseline = self.store.latest_baseline()
            scan_id = (self.scan or {}).get("id", 0)
            observation["id"] = self.store.save_observation(
                observation,
                anchor_img=anchor_img,
                baseline_id=(baseline or {}).get("id"),
                crops=crops,
                scan_id=scan_id,
            )
        except Exception as exc:  # 落库失败不影响本次观测结果
            with runner.lock:
                runner.error = f"落库失败: {type(exc).__name__}: {exc}"

    def _loop(self):
        while True:
            item = self.jobs.get()
            try:
                if item is None:
                    return
                camera_id, epoch = item
                runner = self.runners[camera_id]
                if not runner.owns(epoch):
                    continue
                self._run(runner, epoch)
            except Exception as exc:  # 单次失败不能让 worker 死掉
                runner = self.runners.get(item[0]) if item else None
                if runner is not None:
                    with runner.lock:
                        runner.state = STATE_ERROR
                        runner.error = f"{type(exc).__name__}: {exc}"
                    self._note(runner.id, STATE_ERROR, runner.epoch)
            finally:
                self.jobs.task_done()

    def _run(self, runner, epoch):
        self._set_state(runner, STATE_SETTLING, epoch)
        result = select.collect(runner.source, runner.cfg, cancel=lambda: not runner.owns(epoch))

        if result["status"] == "aborted":
            self._set_state(runner, STATE_ABORTED, epoch)
            return
        if result["status"] != "ok":
            self._commit(
                runner,
                epoch,
                {
                    "camera": runner.id,
                    "tier": consistency.TIER_INVALID,
                    "reasons": result.get("reasons", ["画面未稳定"]),
                    "count": 0,
                    "detections": [],
                    "metrics": {},
                },
            )
            return

        self._set_state(runner, STATE_MEASURING, epoch)
        count = int(runner.cfg.get("observe_frames", DEF_OBSERVE_FRAMES))
        frames, idxs = select.observe_set(result["frames"], result["anchor"], count)
        per_frame = [runner.detector.detect(img) for img in frames]
        report = consistency.evaluate(frames, per_frame, runner.infer_cfg, runner.cfg)
        report["detections"] = sort_detections(report["detections"], runner.order_by)
        observation = {
            "camera": runner.id,
            "name": runner.name,
            "epoch": epoch,
            "tier": report["tier"],
            "reasons": report["reasons"],
            "count": len(report["detections"]),
            "detections": report["detections"],
            "metrics": report["metrics"],
            "frames": {"total": len(result["frames"]), "used": idxs},
        }
        anchor = frames[report["anchor_index"]]
        crops = self._classify(anchor, report["detections"])
        observation["order"] = order_of(report["detections"], runner.order_by)
        self._commit(runner, epoch, observation, anchor_img=anchor, crops=crops)

    def _classify(self, anchor, detections):
        """锚帧上逐个裁剪 → 特征 → 类别；判不出就留 None（下游记作未识别）。返回裁剪图列表。"""
        if self.classifier is None:
            return []
        crops, unknown = [], []
        for det in detections:
            crop = cls_features.crop_normalized(anchor, det["box"], self.classifier.pad)
            crops.append(crop)
            if crop is None:
                det.update({"category_id": None, "category": None, "category_reason": "裁剪无效"})
                continue
            verdict = self.classifier.classify(crop)
            det["category_id"] = verdict["category_id"]
            det["category"] = verdict["category"]
            det["category_reason"] = verdict["reason"]
            det["category_score"] = verdict.get("best_score")
            det["category_margin"] = verdict.get("margin")
            if det["category_id"] in (None, UNKNOWN_ID):
                unknown.append((len(crops) - 1, verdict["feature"]))
        self._assign_clusters(detections, unknown)
        return crops

    def _assign_clusters(self, detections, unknown):
        """未识别目标按特征聚簇：同簇在确认表里共用一个候选名，但仍可逐瓶改。"""
        if not unknown:
            return
        threshold = float(self.pipeline_cfg.get("cluster_threshold", DEF_THRESHOLD))
        members, _ = cluster_vectors([vector for _, vector in unknown], threshold)
        for slot, group in enumerate(members, start=1):
            for position in group:
                detections[unknown[position][0]]["cluster"] = slot
