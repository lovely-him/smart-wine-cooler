"""本地网页服务：触发 / 状态 / 视图 / 确认 / 删除 / 导出。

只用标准库 http.server——内网单人使用，不引框架。板上与宿主跑的是同一份代码。
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image

from services import config as cfg_mod
from services.classify import load_classifier
from services.infer import load_detector, load_embedder
from services.pipeline.runner import PipelineService
from services.store.repo import ORDER_LEFT_RIGHT, UNKNOWN_ID, Store, position_key
from services.vision import load_vision

# ── 路径与常量 ──
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EXPORT_TIMEOUT = 20
UNNAMED_PREFIX = "未命名"


# ── 应用 ──
class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg["store"]["path"], cfg["store"]["data_dir"])
        self.detector = load_detector(cfg["infer"])
        self.embedder = load_embedder(cfg["embed"]) if cfg.get("embed") else None
        self.classifier = (
            load_classifier(cfg.get("classify"), self.store, self.embedder)
            if self.embedder is not None
            else None
        )
        self.service = PipelineService(cfg, self.detector, store=self.store, classifier=self.classifier)
        self.vision = load_vision(cfg.get("vision")) if cfg.get("vision") else None
        self.order_by = {
            cam["id"]: (cam.get("capture") or {}).get("order", ORDER_LEFT_RIGHT)
            for cam in cfg["cameras"]
        }
        self.lock = threading.Lock()

    def close(self):
        self.service.shutdown()
        self.detector.close()
        if self.embedder is not None:
            self.embedder.close()
        if self.vision is not None:
            self.vision.close()

    def suggest(self, camera=None):
        """对当前状态里的未识别目标出候选名：**一簇只问一次**，同簇共用候选（仍可逐瓶改）。"""
        if self.vision is None:
            raise ValueError("未开启视觉大模型（vision.enabled=false 或没有 SILICONFLOW_API_KEY）")
        items = []
        for obs in self.store.current_observations():
            if camera and obs["camera"] != camera:
                continue
            clusters = {}
            for index, det in enumerate(obs["detections"]):
                if det.get("category_id") not in (None, UNKNOWN_ID):
                    continue
                path = det.get("crop")
                if not path or not os.path.isfile(path):
                    continue
                clusters.setdefault(int(det.get("cluster") or 0), []).append((index, path))
            for cluster, members in sorted(clusters.items()):
                with Image.open(members[0][1]) as img:
                    result = self.vision.describe(img.convert("RGB"))
                items.append(
                    {
                        "camera": obs["camera"],
                        "cluster": cluster,
                        "indexes": [index for index, _ in members],
                        "candidate": (result or {}).get("name", ""),
                        "confidence": (result or {}).get("confidence", 0.0),
                        "note": "" if result else "没猜出来，请手填",
                    }
                )
        return {
            "items": items,
            "calls": self.vision.calls,
            "limit": self.vision.daily_limit,
            "blocked": self.vision.blocked,
            "source": self.vision.provider.name,
        }

    def reload_classifier(self):
        """改名/合并/删除后内存索引里的名字与分组都过期了，必须重建。"""
        if self.classifier is not None:
            self.classifier.reload()

    def status(self):
        pending = self.store.pending_diff()
        return {
            "cameras": self.service.status(),
            "scan": self.service.scan_snapshot(),
            "pending": pending,
            "baseline_names": self.store.resolve((pending["baseline"] or {}).get("payload", {})),
            "reported_names": self.store.resolve(pending["reported"]),
            "diff_names": self.store.resolve_diff(pending["diff"]),
            "categories": self.store.category_list(),
            "export_url": self.cfg["export"].get("url", ""),
        }

    def view(self):
        pending = self.store.pending_diff()
        baseline_order = (pending["baseline"] or {}).get("order") or {}
        current_order = self.store.current_order()
        return {
            "baseline": self.store.latest_baseline(),
            "baseline_names": self.store.resolve((pending["baseline"] or {}).get("payload", {})),
            "reported": self.store.reported_view(),
            "reported_names": self.store.resolve(pending["reported"]),
            "diff": pending["diff"],
            "diff_names": self.store.resolve_diff(pending["diff"]),
            "ready": pending["ready"],
            "confirmed": pending["confirmed"],
            "scan": pending["scan"],
            "observations": self.store.current_observations(),
            "draft": self.draft(),
            "baseline_order": self.store.resolve_order(baseline_order),
            "current_order": self.store.resolve_order(current_order),
            "order_only": self.store.order_only(baseline_order, current_order),
        }

    def draft(self):
        """逐瓶草稿：每层的每个检出，带裁剪图地址、预填名与簇号。未命名的叫「未命名-K」。"""
        out = {}
        for obs in self.store.current_observations():
            rows = []
            for index, det in enumerate(obs["detections"]):
                known = det.get("category_id") not in (None, UNKNOWN_ID)
                cluster = det.get("cluster")
                name = det.get("category") if known else ""
                if not name:
                    name = f"{UNNAMED_PREFIX}-{cluster}" if cluster else UNNAMED_PREFIX
                rows.append(
                    {
                        "index": index,
                        "crop_url": f"/img/{obs['id']}/{index}.jpg" if det.get("crop") else "",
                        "name": name,
                        "known": known,
                        "cluster": cluster,
                        "score": det.get("category_score"),
                    }
                )
            out[obs["camera"]] = rows
        return out

    def confirm(self, items, note):
        """逐瓶确认：每瓶一条 {index, name, ignore}；`index` 为空表示**人工补的漏检**。

        真名 → 取/建类别 + **用这一瓶自己的裁剪图**入库；未命名 → 计入「未识别」；忽略 → 不计入。
        补位没有裁剪图也没有位置：只计数量，不进参考库、不进位置序列。
        """
        observations = {obs["camera"]: obs for obs in self.store.current_observations()}
        if not observations:
            raise ValueError("还没有完成的盘点，无法确认（先点「开始盘点」）")
        submitted = {}
        order = {}
        added = 0
        named = 0
        for layer, bottles in (items or {}).items():
            counts = {}
            sequence = []
            observation = observations.get(layer)
            detections = observation["detections"] if observation else []
            order_by = self.order_by.get(layer, ORDER_LEFT_RIGHT)
            for bottle in bottles:
                if bottle.get("ignore"):
                    continue
                index = bottle.get("index")
                name = (bottle.get("name") or "").strip()
                if index is None and not name:
                    continue
                if not name or name.startswith(UNNAMED_PREFIX):
                    category_id = UNKNOWN_ID
                else:
                    category_id = self.store.get_or_create_category(name)
                    added += self._ingest_bottle(observation, index, category_id)
                    named += 1
                counts[str(category_id)] = counts.get(str(category_id), 0) + 1
                if isinstance(index, int) and 0 <= index < len(detections):
                    position = position_key(detections[index], order_by)
                    sequence.append((position, str(category_id)))
            sequence.sort(key=lambda pair: pair[0])
            submitted[layer] = counts
            order[layer] = [category_id for _, category_id in sequence]
        result = self.store.confirm(submitted, note, order)
        result["ingest"] = {"added": added, "named": named}
        return result

    def _ingest_bottle(self, observation, index, category_id):
        """用这一瓶自己的裁剪图入库——逐瓶之后不再需要"猜哪张图对应哪个名字"。"""
        if self.classifier is None or observation is None or index is None:
            return 0
        detections = observation["detections"]
        if not 0 <= index < len(detections):
            return 0
        path = detections[index].get("crop")
        if not path or not os.path.isfile(path):
            return 0
        with Image.open(path) as img:
            vector = self.classifier.feature(img.convert("RGB"))
        ok, _ = self.classifier.add_reference(category_id, vector, path, "confirm", 1.0)
        return 1 if ok else 0

    def history(self, limit=30):
        return {
            "observations": self.store.list_observations(limit),
            "events": self.store.list_events(limit),
            "confirmations": self.store.list_confirmations(limit),
        }

    def export(self, url):
        target = url or self.cfg["export"].get("url", "")
        if not target:
            raise ValueError("没有指定远端接口（配置 export.url 或请求里带 url）")
        import requests

        payload = self.store.export_bundle()
        resp = requests.post(target, json=payload, timeout=EXPORT_TIMEOUT)
        return {"status": resp.status_code, "bytes": len(json.dumps(payload))}


# ── HTTP ──
def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "aicam/0.1"

        def log_message(self, fmt, *args):  # 静音默认访问日志
            return

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, self._file("index.html"), "text/html; charset=utf-8")
                if path == "/api/status":
                    return self._send(200, app.status())
                if path == "/api/view":
                    return self._send(200, app.view())
                if path == "/api/history":
                    return self._send(200, app.history())
                if path == "/api/categories":
                    return self._send(200, {"categories": app.store.category_list()})
                if path.startswith("/img/"):
                    return self._image(path[len("/img/"):])
                return self._send(404, {"error": "not found"})
            except Exception as exc:
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            try:
                if path == "/api/scan/start":
                    return self._send(200, app.service.start_scan())
                if path == "/api/scan/abort":
                    return self._send(200, app.service.abort_scan())
                if path == "/api/confirm":
                    items = body.get("items")
                    if not isinstance(items, dict):
                        return self._send(400, {"error": "items 必须是 {层: [瓶子...]}"})
                    return self._send(200, app.confirm(items, body.get("note", "")))
                if path == "/api/suggest":
                    return self._send(200, app.suggest(body.get("camera") or None))
                if path == "/api/categories/rename":
                    name = app.store.rename_category(int(body["id"]), body["name"])
                    app.reload_classifier()
                    return self._send(200, {"ok": name})
                if path == "/api/categories/merge":
                    target = app.store.merge_categories(int(body["from"]), int(body["to"]))
                    app.reload_classifier()
                    return self._send(200, {"ok": target})
                if path == "/api/categories/delete":
                    target = int(body["id"])
                    app.store.delete_category(target)
                    app.reload_classifier()
                    return self._send(200, {"id": target, "deleted": True})
                if path == "/api/purge":
                    keep = int(body.get("keep_observations", 0))
                    app.store.purge(keep)
                    return self._send(200, {"ok": True})
                if path == "/api/export":
                    return self._send(200, app.export(body.get("url", "")))
                return self._send(404, {"error": "not found"})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def _file(self, name):
            with open(os.path.join(STATIC, name), "rb") as fp:
                return fp.read()

        def _image(self, name):
            """`/img/<obs>.jpg` 返回锚帧；`/img/<obs>/<i>.jpg` 返回第 i 个瓶子的裁剪图。"""
            parts = name.split(".")[0].split("/")
            try:
                obs_id = int(parts[0])
                crop_index = int(parts[1]) if len(parts) > 1 else None
            except (ValueError, IndexError):
                return self._send(400, {"error": "bad id"})
            for obs in app.store.list_observations(200):
                if obs["id"] != obs_id:
                    continue
                if crop_index is None:
                    path = obs["image"]
                else:
                    detections = obs["detections"]
                    path = detections[crop_index].get("crop") if 0 <= crop_index < len(detections) else ""
                if path and os.path.isfile(path):
                    with open(path, "rb") as fp:
                        return self._send(200, fp.read(), "image/jpeg")
                return self._send(404, {"error": "no image"})
            return self._send(404, {"error": "no image"})

    return Handler


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    args = parser.parse_args()

    try:
        cfg = cfg_mod.load(args.config)
        app = App(cfg)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    host, port = cfg["web"]["host"], int(cfg["web"]["port"])
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"[ OK ] 网页已启动: http://{host}:{port}/   摄像头: {', '.join(app.service.runners)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
