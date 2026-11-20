"""存储层的读写封装：观测、基线、确认记录、事件、导出与清理。

约定来自 docs/inventory-loop.md：
- 观测只追加，**存看到的计数、不存差值**（差值随基线换代失效，历史会变成无法解释的数字）
- 差异是派生量，随时可用"观测 + 当时对比的基线版本"重算
- 确认记录保存系统报的与用户提交的两份全文，这是后续改进的原料
"""

import json
import os
import shutil
import threading
import time

import numpy as np

from services.store import db

# ── 路径与常量 ──
UNKNOWN_ID = 0
UNKNOWN_NAME = "未识别"
UNKNOWN_CATEGORY = UNKNOWN_NAME
SCAN_RUNNING = "running"
SCAN_DONE = "done"
SCAN_ABORTED = "aborted"
SCAN_FAILED = "failed"
ORDER_LEFT_RIGHT = "left_to_right"
ORDER_BOTTOM_TOP = "bottom_to_top"


# ── 函数 ──
def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def counts_of(detections):
    """按**类别 id** 计数（未识别归到 UNKNOWN_ID），不按名字。

    名字可以改、可以合并，用名字做键会让历史在改名后断开。
    """
    items = {}
    for det in detections:
        key = str(det.get("category_id") or UNKNOWN_ID)
        items[key] = items.get(key, 0) + 1
    return items


def center_x(detection):
    box = detection.get("box") or [0, 0, 0, 0]
    return (box[0] + box[2]) / 2.0


def center_y(detection):
    box = detection.get("box") or [0, 0, 0, 0]
    return (box[1] + box[3]) / 2.0


def position_key(detection, order_by=ORDER_LEFT_RIGHT):
    """排序键：机位转了 90° 时，“从柜门往里看”对应图片的从下往上（图片 y 向下增长）。

    带次轴：同主轴的两个框（竖直排列 / 同一列）必须有确定的先后，否则顺序取决于模型输出顺序。
    """
    if order_by == ORDER_BOTTOM_TOP:
        return (-center_y(detection), center_x(detection))
    return (center_x(detection), center_y(detection))


def sort_detections(detections, order_by=ORDER_LEFT_RIGHT):
    """按该层的展示方向排好：**落库顺序、逐瓶卡片顺序、位置序列**三处共用这一份顺序。"""
    return sorted(detections or [], key=lambda det: position_key(det, order_by))


def order_of(detections, order_by=ORDER_LEFT_RIGHT):
    """按指定方向的类别序列（未识别记 "0"）。位置只用于展示核对，不参与差异判定。"""
    return [
        str(det.get("category_id") or UNKNOWN_ID)
        for det in sort_detections(detections, order_by)
    ]


def diff_items(reported, submitted):
    """逐层逐类别的净差：只列有变化的条目，带动作。"""
    diffs = []
    layers = set(reported) | set(submitted)
    for layer in sorted(layers):
        left = reported.get(layer) or {}
        right = submitted.get(layer) or {}
        for name in sorted(set(left) | set(right)):
            before = left.get(name, 0)
            after = right.get(name, 0)
            if before == after:
                continue
            diffs.append(
                {
                    "layer": layer,
                    "category": name,
                    "reported": before,
                    "submitted": after,
                    "delta": after - before,
                }
            )
    return diffs


# ── 类 ──
class Store:
    def __init__(self, path, data_dir):
        self.path = path
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.conn = db.connect(path)

    # ── 类别 ──
    def ensure_category(self, name):
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO category (name, created_at) VALUES (?, ?)", (name, now())
            )
            self.conn.commit()

    def categories(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT name FROM category ORDER BY name"
            ).fetchall()
        return [row["name"] for row in rows]

    def category_names(self):
        """{id: 名字}，包含已归档的（历史展示还要用）。"""
        with self.lock:
            rows = self.conn.execute("SELECT id, name FROM category").fetchall()
        return {row["id"]: row["name"] for row in rows}

    def _labeler(self):
        """id → 展示名；先把已合并的旧 id 折叠到目标类别，再取名字。"""
        names = self.category_names()
        mapping = self.merge_map()

        def label(category_id):
            seen = set()
            while category_id in mapping and category_id not in seen:
                seen.add(category_id)
                category_id = mapping[category_id]
            if category_id == UNKNOWN_ID:
                return UNKNOWN_NAME
            return names.get(category_id, UNKNOWN_NAME)

        return label

    def resolve(self, payload):
        """把 {id: 数量} 转成 {名字: 数量}，供界面展示。"""
        label = self._labeler()
        out = {}
        for layer, items in (payload or {}).items():
            merged = {}
            for key, count in items.items():
                name = label(int(key))
                merged[name] = merged.get(name, 0) + count
            out[layer] = merged
        return out

    def resolve_diff(self, diff):
        """差异条目里的类别换成名字。`diff_items()` 返回的是列表，不是按层分的字典。"""
        return self._named_diffs(diff)

    def category_list(self):
        """界面/接口用的类别清单：只有在用类别，附参考图数量。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT c.id, c.name,"
                " (SELECT COUNT(*) FROM reference r WHERE r.category_id = c.id) AS refs"
                " FROM category c ORDER BY c.name"
            ).fetchall()
        return [dict(row) for row in rows]

    def id_of(self, name, create=True):
        """名字 → 类别 id；“未识别”固定映射到 UNKNOWN_ID。"""
        if name == UNKNOWN_NAME:
            return UNKNOWN_ID
        if create:
            return self.get_or_create_category(name)
        with self.lock:
            row = self.conn.execute("SELECT id FROM category WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    def rename_category(self, category_id, new_name):
        """改名：历史里存的是 id，所以改名后历史自动跟随。"""
        with self.lock:
            self.conn.execute(
                "UPDATE category SET name = ? WHERE id = ?", (new_name, category_id)
            )
            self.conn.commit()
        return new_name

    def merge_categories(self, from_id, to_id):
        """把 from 并入 to：参考图改指向、写重定向日志、删掉源类别行。

        源行必须删——留着它就是列表里一个既不能识别、又无法操作的死类别。
        历史解析靠 merge_log 折叠 id，不依赖那一行还在。
        """
        if from_id == to_id:
            raise ValueError("不能合并到自己")
        with self.lock:
            self.conn.execute("UPDATE reference SET category_id = ? WHERE category_id = ?", (to_id, from_id))
            self.conn.execute(
                "INSERT INTO merge_log (created_at, from_category, to_category) VALUES (?, ?, ?)",
                (now(), from_id, to_id),
            )
            self.conn.execute("DELETE FROM category WHERE id = ?", (from_id,))
            self.conn.commit()
        return to_id

    def delete_category(self, category_id):
        """硬删：参考图一并删除；历史里引用它的条目退化为「未识别」。"""
        with self.lock:
            self.conn.execute("DELETE FROM reference WHERE category_id = ?", (category_id,))
            self.conn.execute("DELETE FROM category WHERE id = ?", (category_id,))
            self.conn.commit()
        return category_id

    def merge_map(self):
        """{被合并掉的 id: 合并目标 id}，展示历史时把旧 id 解析到新类别。"""
        with self.lock:
            rows = self.conn.execute("SELECT from_category, to_category FROM merge_log").fetchall()
        return {row["from_category"]: row["to_category"] for row in rows}

    def get_or_create_category(self, name):
        with self.lock:
            row = self.conn.execute("SELECT id FROM category WHERE name = ?", (name,)).fetchone()
            if row is not None:
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO category (name, created_at) VALUES (?, ?)", (name, now())
            )
            self.conn.commit()
            return cur.lastrowid

    # ── 参考图 ──
    def add_reference_by_id(self, category_id, vector, path, source, quality=0.0):
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self.lock:
            self.conn.execute(
                "INSERT INTO reference (category_id, path, feature, dim, source, quality, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (category_id, path, vec.tobytes(), vec.shape[0], source, float(quality), now()),
            )
            self.conn.commit()
        return category_id

    def add_reference(self, category_name, vector, path, source, quality=0.0):
        return self.add_reference_by_id(
            self.get_or_create_category(category_name), vector, path, source, quality
        )

    def reference_groups(self):
        """[{category_id, name, vector}]，供分类器建内存索引。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT c.id AS cid, c.name AS name, r.feature AS feature, r.dim AS dim"
                " FROM reference r JOIN category c ON c.id = r.category_id"
                " ORDER BY r.id"
            ).fetchall()
        groups = []
        for row in rows:
            vector = np.frombuffer(row["feature"], dtype=np.float32)
            if vector.size != row["dim"]:
                continue
            groups.append({"category_id": row["cid"], "name": row["name"], "vector": vector})
        return groups

    def reference_count(self):
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM reference").fetchone()
        return row["n"]

    # ── 基线 ──
    def latest_baseline(self):
        with self.lock:
            row = self.conn.execute("SELECT * FROM baseline ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "note": row["note"],
            "payload": json.loads(row["payload"]),
            "order": json.loads(row["order_payload"] or "{}"),
        }

    def _insert_baseline(self, payload, note, order=None):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO baseline (created_at, note, payload, order_payload) VALUES (?, ?, ?, ?)",
                (
                    now(),
                    note,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(order or {}, ensure_ascii=False),
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    # ── 观测 ──
    def save_observation(self, obs, anchor_img=None, baseline_id=None, crops=None, scan_id=0):
        detections = obs.get("detections", [])
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO observation (scan_id, camera, epoch, created_at, tier, reasons, count,"
                " detections, metrics, frames, baseline_id, image, order_payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan_id,
                    obs["camera"],
                    obs.get("epoch", 0),
                    now(),
                    obs["tier"],
                    json.dumps(obs.get("reasons", []), ensure_ascii=False),
                    obs.get("count", 0),
                    json.dumps(detections, ensure_ascii=False),
                    json.dumps(obs.get("metrics", {}), ensure_ascii=False),
                    json.dumps(obs.get("frames", {}), ensure_ascii=False),
                    baseline_id,
                    "",
                    json.dumps(obs.get("order", []), ensure_ascii=False),
                ),
            )
            obs_id = cur.lastrowid
            self.conn.commit()

        folder = os.path.join(self.data_dir, "observations", str(obs_id))
        image_path = ""
        if anchor_img is not None or crops:
            os.makedirs(folder, exist_ok=True)
        if anchor_img is not None:
            image_path = os.path.join(folder, "anchor.jpg")
            anchor_img.save(image_path, quality=88)
        for index, crop in enumerate(crops or []):
            if crop is None or index >= len(detections):
                continue
            path = os.path.join(folder, f"crop_{index}.jpg")
            crop.save(path, quality=90)
            detections[index]["crop"] = path
        if anchor_img is not None or crops:
            with self.lock:
                self.conn.execute(
                    "UPDATE observation SET image = ?, detections = ? WHERE id = ?",
                    (image_path, json.dumps(detections, ensure_ascii=False), obs_id),
                )
                self.conn.commit()
        return obs_id

    def list_observations(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM observation ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._observation(row) for row in rows]

    def _observation(self, row):
        return {
            "id": row["id"],
            "scan_id": row["scan_id"],
            "camera": row["camera"],
            "epoch": row["epoch"],
            "created_at": row["created_at"],
            "tier": row["tier"],
            "reasons": json.loads(row["reasons"]),
            "count": row["count"],
            "detections": json.loads(row["detections"]),
            "metrics": json.loads(row["metrics"]),
            "frames": json.loads(row["frames"]),
            "baseline_id": row["baseline_id"],
            "image": row["image"],
            "order": json.loads(row["order_payload"] or "[]"),
        }

    # ── 扫描 ──
    def open_scan(self):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO scan (started_at, state) VALUES (?, 'running')", (now(),)
            )
            self.conn.commit()
            return cur.lastrowid

    def close_scan(self, scan_id, state, note=""):
        with self.lock:
            self.conn.execute(
                "UPDATE scan SET finished_at = ?, state = ?, note = ? WHERE id = ?",
                (now(), state, note, scan_id),
            )
            self.conn.commit()
        return state

    def scans(self, limit=20):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM scan ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def latest_scan(self, state=None):
        sql = "SELECT * FROM scan"
        args = []
        if state:
            sql += " WHERE state = ?"
            args.append(state)
        sql += " ORDER BY id DESC LIMIT 1"
        with self.lock:
            row = self.conn.execute(sql, tuple(args)).fetchone()
        return dict(row) if row else None

    def observations_of_scan(self, scan_id):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM observation WHERE scan_id = ? ORDER BY camera", (scan_id,)
            ).fetchall()
        return [self._observation(row) for row in rows]

    def current_observations(self):
        """当前状态：最近一次**完成**的扫描的各层观测。"""
        scan = self.latest_scan(SCAN_DONE)
        return self.observations_of_scan(scan["id"]) if scan else []

    def current_order(self):
        """当前状态里各层的位置序列（落库时已按该层的排序方向排好）。"""
        return {obs["camera"]: obs["order"] for obs in self.current_observations()}

    def resolve_order(self, order):
        """序列里的 id 换成展示名。"""
        label = self._labeler()
        return {
            layer: [label(int(category_id)) for category_id in seq]
            for layer, seq in (order or {}).items()
        }

    def order_only(self, baseline_order, current_order):
        """计数相同、元素相同、只有顺序不同 → {层: True}。这种层不产生差异行。"""
        out = {}
        for layer, current in (current_order or {}).items():
            base = (baseline_order or {}).get(layer)
            if base is None or len(base) != len(current):
                continue
            if sorted(base) == sorted(current) and base != current:
                out[layer] = True
        return out

    # ── 事件 ──
    def add_event(self, scan_id, baseline_id, note="", counts=None):
        with self.lock:
            self.conn.execute(
                "INSERT INTO event (created_at, scan_id, camera, observation_id, baseline_id, note)"
                " VALUES (?, ?, '', 0, ?, ?)",
                (now(), scan_id, baseline_id, note),
            )
            self.conn.commit()
            row_id = self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return row_id

    def list_events(self, limit=50):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def set_event_note(self, event_id, note):
        with self.lock:
            self.conn.execute("UPDATE event SET note = ? WHERE id = ?", (note, event_id))
            self.conn.commit()

    # ── 浏览与差异 ──
    def reported_view(self):
        """当前状态（最近一次完成的扫描）里各层看到的。"""
        view = {}
        for obs in self.current_observations():
            view[obs["camera"]] = counts_of(obs["detections"])
        return view

    def pending_diff(self):
        baseline = self.latest_baseline()
        scan = self.latest_scan(SCAN_DONE)
        reported = self.reported_view()
        base_payload = (baseline or {}).get("payload", {})
        confirmed = self.scan_confirmed(scan)
        return {
            "baseline": baseline,
            "scan": scan,
            "ready": scan is not None,
            "reported": reported,
            "confirmed": confirmed,
            "diff": [] if confirmed else (diff_items(base_payload, reported) if scan else []),
        }

    def scan_confirmed(self, scan):
        """这次扫描是否已经被确认过。

        确认的语义是"我核对过了，柜子现在就是这样"。所以确认之后再拿系统当时的观测去和
        新基线比，等于否定用户的确认——差异必须清零，直到出现**新的**扫描。
        判据是确认记录里存的观测 id 是否覆盖这次扫描的全部观测（而不是比时间戳，避免时钟跳变）。
        """
        if not scan:
            return False
        latest = self.list_confirmations(1)
        if not latest:
            return False
        confirmed_ids = set(latest[0].get("observation_ids") or [])
        current_ids = {obs["id"] for obs in self.observations_of_scan(scan["id"])}
        return bool(current_ids) and current_ids <= confirmed_ids

    # ── 确认 ──
    def confirm(self, submitted_ids, note="", order=None):
        """收的已经是 {层: {类别 id 字符串: 数量}}；逐瓶到计数的转换在调用方做。

        记两份差异：corrections = 用户对系统报的修改（纠错信号）；
        baseline_delta = 相对旧基线的净变化（用户实际看到的那一份）。
        """
        reported = self.reported_view()
        previous = (self.latest_baseline() or {}).get("payload", {})
        initial = not previous
        corrections = diff_items(reported, submitted_ids)
        baseline_delta = diff_items(previous, submitted_ids)
        obs_ids = [obs["id"] for obs in self.current_observations()]
        baseline_id = self._insert_baseline(submitted_ids, note, order)
        with self.lock:
            self.conn.execute(
                "INSERT INTO confirmation (created_at, baseline_id, reported, submitted,"
                " corrections, baseline_delta, observation_ids, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now(),
                    baseline_id,
                    json.dumps(reported, ensure_ascii=False),
                    json.dumps(submitted_ids, ensure_ascii=False),
                    json.dumps(corrections, ensure_ascii=False),
                    json.dumps(baseline_delta, ensure_ascii=False),
                    json.dumps(obs_ids),
                    note,
                ),
            )
            self.conn.commit()
        return {
            "baseline_id": baseline_id,
            "initial": initial,
            "corrections": corrections,
            "baseline_delta": baseline_delta,
            "observation_ids": obs_ids,
            "submitted_ids": submitted_ids,
        }

    def _named_diffs(self, diffs):
        """确认记录里的差异条目存的是类别 id，展示前换成当前名字。"""
        label = self._labeler()
        out = []
        for item in diffs or []:
            entry = dict(item)
            try:
                cid = int(entry.get("category"))
            except (TypeError, ValueError):
                out.append(entry)
                continue
            entry["category_id"] = cid
            entry["category"] = label(cid)
            out.append(entry)
        return out

    def list_confirmations(self, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM confirmation ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "baseline_id": row["baseline_id"],
                "reported": json.loads(row["reported"]),
                "submitted": json.loads(row["submitted"]),
                "corrections": self._named_diffs(json.loads(row["corrections"])),
                "baseline_delta": self._named_diffs(json.loads(row["baseline_delta"])),
                "observation_ids": json.loads(row["observation_ids"]),
                "note": row["note"],
            }
            for row in rows
        ]

    # ── 导出与清理 ──
    def export_bundle(self, limit=200):
        return {
            "exported_at": now(),
            "categories": self.categories(),
            "baseline": self.latest_baseline(),
            "observations": self.list_observations(limit),
            "confirmations": self.list_confirmations(limit),
            "events": self.list_events(limit),
        }

    def purge(self, keep_observations=0):
        """手动删除历史：清空事件/扫描/观测/确认记录，保留基线与类别。"""
        with self.lock:
            self.conn.execute("DELETE FROM event")
            self.conn.execute("DELETE FROM scan")
            self.conn.execute("DELETE FROM confirmation")
            rows = self.conn.execute(
                "SELECT id FROM observation ORDER BY id DESC LIMIT ?", (keep_observations,)
            ).fetchall()
            keep = {row["id"] for row in rows}
            if keep:
                placeholders = ",".join("?" for _ in keep)
                self.conn.execute(f"DELETE FROM observation WHERE id NOT IN ({placeholders})", tuple(keep))
            else:
                self.conn.execute("DELETE FROM observation")
            self.conn.commit()

        folder = os.path.join(self.data_dir, "observations")
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if not name.isdigit() or int(name) not in keep:
                    shutil.rmtree(os.path.join(folder, name), ignore_errors=True)
        return True
