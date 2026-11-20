"""静态图回归测试：用真实图片把「观测 → 确认 → 基线 → 差异」整条闭环跑一遍。

这不是单元测试而是**系统回归**：只替换采集源（图片目录代替摄像头），其余全部走真实代码路径。
换模型、改阈值、改流水线之后跑它，能立刻看出结论有没有被改坏。

用法:
    python -m services.regress [--config config/app.json]
"""

import argparse
import copy
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import requests
from PIL import Image

from services import config as cfg_mod
from services.infer import load_detector
from services.pipeline.runner import PipelineService
from services.store.repo import (
    ORDER_BOTTOM_TOP,
    ORDER_LEFT_RIGHT,
    SCAN_ABORTED,
    SCAN_DONE,
    SCAN_FAILED,
    UNKNOWN_CATEGORY,
    UNKNOWN_ID,
    Store,
    order_of,
    sort_detections,
)
from services.vision import Vision, load_vision
from services.vision.siliconflow import SiliconFlow

# ── 路径与常量 ──
WAIT = 60.0
UNK = str(UNKNOWN_ID)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tmp", "20261117134147")
# 固定输入：这两张黄金图的检出数是已知且稳定的（3 瓶 / 2 瓶，conf 0.30）
THREE_BOTTLES = "TV CAM 设备_20261117_142717.348.jpg"
TWO_BOTTLES = "TV CAM 设备_20261117_135643.870.jpg"
MIXED = "TV CAM 设备_20261117_142612.005.jpg"


# ── 函数 ──
def make_input(workdir, name, tag):
    """自建输入目录：回归的输入必须由自己生成，不能依赖外部手工目录。"""
    folder = os.path.join(workdir, "in" + tag)
    os.makedirs(folder, exist_ok=True)
    with Image.open(os.path.join(GOLDEN, name)) as fp:
        fp.convert("RGB").save(os.path.join(folder, "frame.jpg"), quality=95)
    return folder


# ── 函数 ──
def check(ok, message):
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    return 0 if ok else 1


def wait_scan(service, timeout=WAIT):
    """等队列清空 + 等扫描判定结束，返回扫描快照。"""
    service.wait(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = service.scan_snapshot()
        if snap["state"] in (SCAN_DONE, SCAN_FAILED, SCAN_ABORTED):
            return snap
        time.sleep(0.02)
    return service.scan_snapshot()


def observe(cfg, detector, store, source_dir, delay=0.0, order=None):
    """用给定图片目录跑一次完整整柜扫描，返回该层观测。"""
    run_cfg = dict(cfg)
    capture = {"backend": "image_dir", "source": source_dir, "delay": delay}
    if order:
        capture["order"] = order
    run_cfg["cameras"] = [
        {
            "id": "layer1",
            "name": "回归层",
            "capture": capture,
        }
    ]
    service = PipelineService(run_cfg, detector, store=store)
    try:
        service.start_scan()
        wait_scan(service)
        return service.status()[0]["observation"]
    finally:
        service.shutdown()


# ── 离线回退用的本地假服务 ──
def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def silent_server():
    """接受连接但永不回应——用来触发读超时。"""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    held = []

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1], held


class BrokenAPI(BaseHTTPRequestHandler):
    """`/models` 正常返回在役型号，`/chat/completions` 返回非预期格式。"""

    hits = {}

    def log_message(self, fmt, *args):
        return

    def _reply(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        BrokenAPI.hits[self.path] = BrokenAPI.hits.get(self.path, 0) + 1
        self._reply({"data": [{"id": "Qwen/Qwen3-VL-8B-Instruct"}]})

    def do_POST(self):
        BrokenAPI.hits[self.path] = BrokenAPI.hits.get(self.path, 0) + 1
        self._reply({"choices": [{"message": {"content": "我不是 JSON"}}]})


class HookAPI(BaseHTTPRequestHandler):
    """导出落点：把收到的 JSON 留下来，回 200。"""

    last = {}
    hits = 0

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        HookAPI.hits += 1
        try:
            HookAPI.last = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            HookAPI.last = {"__not_json__": True}
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def stage_scan(cfg, detector, workdir, first, second):
    """整柜扫描：全部层完成才算数；任一层没拿到有效观测则整次作废。"""
    print("\n【阶段8】整柜扫描（全部完成才算数）")
    fails = 0
    dark = os.path.join(workdir, "in-dark")
    os.makedirs(dark, exist_ok=True)
    Image.new("RGB", (640, 480), (8, 8, 8)).save(os.path.join(dark, "frame.jpg"), quality=95)

    def run_cfg(sources):
        built = dict(cfg)
        built["cameras"] = [
            {
                "id": camera_id,
                "name": camera_id,
                "capture": {"backend": "image_dir", "source": source, "delay": 0.0},
            }
            for camera_id, source in sources.items()
        ]
        return built

    store = Store(os.path.join(workdir, "scan.db"), workdir)
    try:
        service = PipelineService(run_cfg({"layer1": first, "layer2": second}), detector, store=store)
        try:
            service.start_scan()
            snap = wait_scan(service)
            fails += check(snap["state"] == SCAN_DONE, f"两层都完成 → 扫描完成（实际 {snap['state']}）")
            current = store.current_observations()
            fails += check(len(current) == 2, f"当前状态包含两层（实际 {len(current)}）")
            fails += check(
                set(store.reported_view()) == {"layer1", "layer2"},
                f"当前视图按层汇总（实际 {sorted(store.reported_view())}）",
            )
            fails += check(store.latest_scan(SCAN_DONE) is not None, "完成的扫描已落库")
            fails += check(store.latest_scan(SCAN_FAILED) is None, "没有失败扫描")
            first_scan_id = store.latest_scan(SCAN_DONE)["id"]
            first_events = len(store.list_events())
        finally:
            service.shutdown()

        service = PipelineService(run_cfg({"layer1": first, "layer2": dark}), detector, store=store)
        try:
            service.start_scan()
            snap = wait_scan(service)
            fails += check(snap["state"] == SCAN_FAILED, f"一层拍不到有效画面 → 整次作废（实际 {snap['state']}）")
            fails += check(bool(snap["note"]), f"作废原因写清了（{snap['note']}）")
            fails += check(
                store.latest_scan(SCAN_DONE)["id"] == first_scan_id,
                "当前状态仍是上一次完整扫描",
            )
            fails += check(
                len(store.current_observations()) == 2,
                f"作废的扫描不改变当前状态（仍是 {len(store.current_observations())} 层）",
            )
            fails += check(
                len(store.list_events()) == first_events,
                "作废的扫描不记事件",
            )
        finally:
            service.shutdown()
    finally:
        store.conn.close()
    return fails


def stage_bottles(cfg, workdir, source_dir):
    """逐瓶确认：各瓶独立命名、同名合并计数、未命名计入未识别、忽略不计入、参考图各自入库。"""
    print("\n【阶段9】逐瓶确认与建档")
    from services.web.server import App, make_handler

    fails = 0
    work = os.path.join(workdir, "bottles")
    os.makedirs(work, exist_ok=True)
    run_cfg = copy.deepcopy(cfg)
    run_cfg["store"] = {"path": os.path.join(work, "app.db"), "data_dir": work}
    run_cfg["vision"] = {"enabled": False, "cache": os.path.join(work, "vision.json")}
    run_cfg["cameras"] = [
        {
            "id": "layer1",
            "name": "回归层",
            "capture": {"backend": "image_dir", "source": source_dir, "delay": 0.0},
        }
    ]
    app = App(run_cfg)
    try:
        app.service.start_scan()
        wait_scan(app.service)
        rows = app.draft().get("layer1", [])
        fails += check(len(rows) == 2, f"草稿逐瓶给出 2 条（实际 {len(rows)}）")
        fails += check(all(not row["known"] for row in rows), "空参考库时全部未识别")
        fails += check(
            all(row["name"].startswith("未命名") for row in rows),
            f"预填名为未命名-K（实际 {[row['name'] for row in rows]}）",
        )
        fails += check(
            all(row["cluster"] for row in rows),
            f"未识别目标已分簇（{[row['cluster'] for row in rows]}）",
        )
        fails += check(
            all("/img/" in row["crop_url"] for row in rows), "每条草稿带裁剪图地址"
        )

        bottles = [
            {"index": rows[0]["index"], "name": "回归红酒", "ignore": False},
            {"index": rows[1]["index"], "name": "回归椰子水", "ignore": False},
        ]
        result = app.confirm({"layer1": bottles}, "逐瓶建档")
        red = app.store.id_of("回归红酒", create=False)
        coconut = app.store.id_of("回归椰子水", create=False)
        fails += check(
            red is not None and coconut is not None and red != coconut,
            "一次提交建了两个不同类别",
        )
        fails += check(
            result["submitted_ids"] == {"layer1": {str(red): 1, str(coconut): 1}},
            f"两瓶分别计入各自类别（实际 {result['submitted_ids']}）",
        )
        fails += check(result["ingest"]["added"] == 2, f"两瓶各入库一张（实际 {result['ingest']['added']}）")
        fails += check(
            app.store.reference_count() == 2, f"参考库共 2 张（实际 {app.store.reference_count()}）"
        )

        # 确认的语义是"我核对过了，柜子现在是这样"：系统当时报的是「未识别」，
        # 用户提交的是两个具名类别，确认之后差异必须立刻清零，而不是等下一次扫描。
        pending = app.store.pending_diff()
        fails += check(pending["confirmed"], "确认后本次扫描标记为已确认")
        fails += check(
            pending["diff"] == [], f"确认后差异立即清零（实际 {pending['diff']}）"
        )
        fails += check(
            app.view()["confirmed"] is True, "视图接口也带出已确认标记"
        )

        app.service.start_scan()
        wait_scan(app.service)
        fails += check(
            not app.store.pending_diff()["confirmed"], "出现新扫描后重新变为待确认"
        )
        rows2 = app.draft().get("layer1", [])
        named = {row["name"] for row in rows2 if row["known"]}
        fails += check(len(rows2) == 2, f"再次盘点仍 2 瓶（实际 {len(rows2)}）")
        fails += check(
            named == {"回归红酒", "回归椰子水"},
            f"两瓶各自认出对应类别（实际 {sorted(named)}）",
        )

        result2 = app.confirm(
            {
                "layer1": [
                    {"index": rows2[0]["index"], "name": "回归红酒", "ignore": False},
                    {"index": rows2[1]["index"], "name": "", "ignore": True},
                ]
            },
            "忽略一瓶",
        )
        fails += check(
            sum(result2["submitted_ids"]["layer1"].values()) == 1,
            f"忽略的那瓶不计入（实际 {result2['submitted_ids']}）",
        )

        empty = app.confirm({"layer1": []}, "空柜子")
        fails += check(
            empty["submitted_ids"] == {"layer1": {}},
            f"空柜子（0 瓶）也能提交成基线（实际 {empty['submitted_ids']}）",
        )
        fails += check(
            app.store.latest_baseline()["payload"] == {"layer1": {}},
            f"空柜子的基线是空计数（实际 {app.store.latest_baseline()['payload']}）",
        )

        hook = ThreadingHTTPServer(("127.0.0.1", 0), HookAPI)
        threading.Thread(target=hook.serve_forever, daemon=True).start()
        try:
            result = app.export(f"http://127.0.0.1:{hook.server_address[1]}/hook")
            fails += check(result["status"] == 200, f"导出推送返回 200（实际 {result['status']}）")
            fails += check(HookAPI.hits == 1, f"只推一次（实际 {HookAPI.hits} 次）")
            fails += check(
                set(HookAPI.last) >= {"exported_at", "categories", "baseline", "observations"},
                f"远端收到的是完整导出包（实际 {sorted(HookAPI.last)}）",
            )
            fails += check(
                bool(HookAPI.last.get("categories")), "导出包里带了类别"
            )
            try:
                app.export(f"http://127.0.0.1:{free_port()}/hook")
                fails += check(False, "导不出去时应该报错")
            except Exception:
                fails += check(True, "导出地址不可达时抛错，不静默成功")
            try:
                app.export("")
                fails += check(False, "没配远端时导出应该报错")
            except ValueError:
                fails += check(True, "没配远端时导出报错")
        finally:
            hook.shutdown()
            hook.server_close()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        api = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            def post(path, payload):
                resp = requests.post(api + path, json=payload, timeout=10)
                return resp.status_code

            def rescan():
                app.service.start_scan()
                wait_scan(app.service)
                return app.draft().get("layer1", [])

            red = app.store.id_of("回归红酒", create=False)
            coconut = app.store.id_of("回归椰子水", create=False)
            code = post("/api/categories/rename", {"id": red, "name": "回归红酒2"})
            fails += check(code == 200, f"改名接口 200（实际 {code}）")
            names = {row["name"] for row in rescan() if row["known"]}
            fails += check(
                names == {"回归红酒2", "回归椰子水"},
                f"改名后下一次扫描立刻用新名字（实际 {sorted(names)}）",
            )

            code = post("/api/categories/merge", {"from": coconut, "to": red})
            fails += check(code == 200, f"合并接口 200（实际 {code}）")
            names = {row["name"] for row in rescan() if row["known"]}
            fails += check(
                names == {"回归红酒2"}, f"合并后两瓶都认成目标类（实际 {sorted(names)}）"
            )

            code = post("/api/categories/delete", {"id": red})
            fails += check(code == 200, f"删除接口 200（实际 {code}）")
            rows3 = rescan()
            fails += check(
                bool(rows3) and all(not row["known"] for row in rows3),
                f"类别删掉后参考图也没了，两瓶回到未识别（实际 {[r['name'] for r in rows3]}）",
            )
        finally:
            httpd.shutdown()
            httpd.server_close()

        app.store.conn.execute("DELETE FROM scan")
        app.store.conn.commit()
        try:
            app.confirm({"layer1": []}, "没有扫描")
            fails += check(False, "没有完成的盘点时确认应被拒绝")
        except ValueError:
            fails += check(True, "没有完成的盘点时确认被拒绝")
    finally:
        app.close()
    return fails


def stage_order(cfg, detector, workdir, source_dir):
    """位置序列：只记录与展示，不进差异判定；元素相同仅顺序不同时标「仅位置不同」。"""
    print("\n【阶段10】位置序列（只展示不判定）")
    fails = 0
    dets = [
        {"box": [0.5, 0.6, 0.8, 1.0], "category_id": 7},
        {"box": [0.1, 0.0, 0.4, 0.4], "category_id": 5},
    ]
    fails += check(
        order_of(dets, ORDER_LEFT_RIGHT) == ["5", "7"],
        f"从左到右（实际 {order_of(dets, ORDER_LEFT_RIGHT)}）",
    )
    fails += check(
        order_of(dets, ORDER_BOTTOM_TOP) == ["7", "5"],
        f"机位转 90° 时从下往上（实际 {order_of(dets, ORDER_BOTTOM_TOP)}）",
    )
    fails += check(
        [det["category_id"] for det in sort_detections(dets, ORDER_LEFT_RIGHT)] == [5, 7],
        "从左到右时检出本身也按序排好（不只看序列）",
    )
    fails += check(
        [det["category_id"] for det in sort_detections(dets, ORDER_BOTTOM_TOP)] == [7, 5],
        "从下往上时检出本身也按序排好",
    )
    fails += check(order_of([{"box": [0.0, 0.0, 1.0, 1.0]}]) == ["0"], "未识别在序列里记 0")

    store = Store(os.path.join(workdir, "order.db"), workdir)
    try:
        obs = observe(cfg, detector, store, source_dir)
        fails += check(obs is not None and obs["count"] == 3, f"观测正常（检出 {obs and obs['count']}）")
        order = store.current_order()
        fails += check(
            len(order.get("layer1", [])) == 3, f"当前状态有 3 个位置的序列（实际 {order.get('layer1')}）"
        )
        store.confirm({"layer1": {UNK: 3}}, note="建档", order={"layer1": ["1", "2", "3"]})
        base = store.latest_baseline()
        fails += check(
            base["order"] == {"layer1": ["1", "2", "3"]}, "基线存下了位置序列"
        )
        fails += check(
            store.order_only({"layer1": ["1", "2"]}, {"layer1": ["2", "1"]}) == {"layer1": True},
            "元素相同仅顺序不同 → 仅位置不同",
        )
        fails += check(
            store.order_only({"layer1": ["1", "1"]}, {"layer1": ["1", "2"]}) == {},
            "元素不同不算仅位置不同",
        )
        fails += check(
            store.order_only({"layer1": ["1", "2", "3"]}, {"layer1": ["1", "2"]}) == {},
            "长度不同不当成位置变化",
        )

        observe(cfg, detector, store, source_dir)
        fails += check(
            store.current_order() == order, "同一场景再扫一次，序列一致"
        )
        fails += check(
            store.order_only(base["order"], store.current_order()) == {},
            "序列一致时不标「仅位置不同」",
        )

        # 落库顺序必须就是展示顺序：逐瓶卡片的 index 由它决定，顺序反了用户看到的编号也是反的
        left = observe(cfg, detector, store, source_dir, order=ORDER_LEFT_RIGHT)
        xs = [(det["box"][0] + det["box"][2]) / 2 for det in left["detections"]]
        fails += check(xs == sorted(xs), f"从左到右时检出按横向中心递增（{xs}）")
        bottom = observe(cfg, detector, store, source_dir, order=ORDER_BOTTOM_TOP)
        ys = [(det["box"][1] + det["box"][3]) / 2 for det in bottom["detections"]]
        fails += check(
            ys == sorted(ys, reverse=True), f"从下往上时检出按纵向中心递减（{ys}）"
        )
    finally:
        store.conn.close()
    return fails


def stage_offline(cfg, detector, workdir, source_dir):
    """大模型不可用时，采集/检测/一致性/差异/确认都必须照常，且不能被拖慢。"""
    print("\n【阶段7】离线回退（连接被拒 / 超时 / 格式错 / 证书错）")
    fails = 0
    dummy = Image.new("RGB", (64, 64), (128, 128, 128))
    key_backup = os.environ.get("SILICONFLOW_API_KEY")
    os.environ["SILICONFLOW_API_KEY"] = "offline-regress-key"
    srv = httpd = None
    held = []
    try:
        rejected = f"http://127.0.0.1:{free_port()}/v1"
        srv, silent_port, held = silent_server()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BrokenAPI)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        http_port = httpd.server_address[1]

        shapes = [
            ("连接被拒", rejected, 1.0),
            ("超时", f"http://127.0.0.1:{silent_port}/v1", 1.0),
            ("返回非预期格式", f"http://127.0.0.1:{http_port}/v1", 1.0),
            ("证书错误", f"https://127.0.0.1:{http_port}/v1", 1.0),
        ]
        for index, (name, base, timeout) in enumerate(shapes):
            provider = SiliconFlow(
                base_url=base, api_key="offline-regress-key", model="", timeout=timeout
            )
            vision = Vision(provider, os.path.join(workdir, f"offline-cache-{index}.json"), 0)
            started = time.time()
            result = vision.describe(dummy)
            elapsed = time.time() - started
            fails += check(result is None, f"{name}: 返回 None 而不是抛异常")
            fails += check(elapsed <= timeout + 2.0, f"{name}: 耗时 {elapsed:.2f}s 有上限")
            fails += check(vision.calls <= 1, f"{name}: 只调用 {vision.calls} 次（不重试）")

        chat_hits = [v for k, v in BrokenAPI.hits.items() if k.endswith("/chat/completions")]
        fails += check(chat_hits and max(chat_hits) == 1, f"格式错场景只请求一次（实际 {chat_hits}）")

        bad = copy.deepcopy(cfg)
        bad["vision"] = dict(
            bad.get("vision") or {},
            enabled=True,
            base_url=rejected,
            timeout=1,
            cache=os.path.join(workdir, "offline-vision.json"),
        )
        fails += check(load_vision(bad["vision"], workdir) is not None, "开启且凭据存在时会真的加载 provider")

        offline_store = Store(os.path.join(workdir, "offline.db"), workdir)
        started = time.time()
        obs = observe(bad, detector, offline_store, source_dir)
        fails += check(obs is not None and obs["count"] == 3, f"观测照常（检出 {obs and obs['count']}）")
        fails += check(obs is not None and obs["tier"] == "可信", f"一致性判断照常（{obs and obs['tier']}）")
        offline_store.confirm({"layer1": {UNK: 3}}, "离线建档")
        fails += check(offline_store.latest_baseline() is not None, "确认建档照常")

        second = offline_store.confirm({"layer1": {UNK: 2}}, "离线再确认")
        fails += check(len(second["baseline_delta"]) >= 1, "再次确认可更新基线")

        elapsed = time.time() - started
        fails += check(elapsed < 20.0, f"整段耗时 {elapsed:.1f}s（不可达的 provider 不拖慢主流程）")
        offline_store.conn.close()

        # 识别必须永远靠本地特征检索：这三个包的源码里不该出现 vision 依赖。
        # 将来接入"运行时建档"时只应改 web/建档那条路径，改动必须同步改这条断言。
        offenders = []
        for package in ("pipeline", "classify"):
            folder = os.path.join(ROOT, "services", package)
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".py"):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as fp:
                    if "vision" in fp.read():
                        offenders.append(f"{package}/{name}")
        fails += check(not offenders, f"识别路径不依赖 vision（涉入 {offenders or '无'}）")

        # 运行时建档的关闭路径：没开启时必须明确报错，而不是静默返回空候选
        try:
            silent = dict(cfg.get("vision") or {}, enabled=False)
            fails += check(load_vision(silent, workdir) is None, "关闭时不会创建 provider（不出网）")
        except Exception as exc:
            fails += check(False, f"关闭路径抛了异常: {type(exc).__name__}: {exc}")
    finally:
        if key_backup is None:
            os.environ.pop("SILICONFLOW_API_KEY", None)
        else:
            os.environ["SILICONFLOW_API_KEY"] = key_backup
        for conn in held:
            conn.close()
        if srv is not None:
            srv.close()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
    return fails


def make_swap(source_dir, target_dir, detector):
    """把画面里首尾两个目标的像素位置对调：元素不变、只有顺序变。"""
    os.makedirs(target_dir, exist_ok=True)
    name = sorted(f for f in os.listdir(source_dir) if f.lower().endswith(".jpg"))[0]
    with Image.open(os.path.join(source_dir, name)) as fp:
        img = fp.convert("RGB")
    width, height = img.size
    rects = []
    for det in detector.detect(img):
        box = det["box"]
        rects.append(
            (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
        )
    rects.sort(key=lambda rect: (rect[1] + rect[3]) / 2)
    head, tail = rects[0], rects[-1]
    out = img.copy()
    piece_head = img.crop(head).resize((tail[2] - tail[0], tail[3] - tail[1]))
    piece_tail = img.crop(tail).resize((head[2] - head[0], head[3] - head[1]))
    out.paste(piece_head, (tail[0], tail[1]))
    out.paste(piece_tail, (head[0], head[1]))
    out.save(os.path.join(target_dir, "frame.jpg"), quality=95)


def stage_order_swap(cfg, workdir, source_dir, detector):
    """端到端「仅位置不同」：两瓶上下对调后再盘一次，只标位置变化，不产生差异行。"""
    print("\n【阶段12】位置互换端到端（仅位置不同）")
    from services.web.server import App

    fails = 0
    work = os.path.join(workdir, "swap")
    swapped = os.path.join(work, "swapped")
    make_swap(source_dir, swapped, detector)

    def build(source):
        run_cfg = copy.deepcopy(cfg)
        run_cfg["store"] = {"path": os.path.join(work, "app.db"), "data_dir": work}
        run_cfg["vision"] = {"enabled": False, "cache": os.path.join(work, "vision.json")}
        run_cfg["cameras"] = [
            {
                "id": "layer1",
                "name": "回归层",
                "capture": {
                    "backend": "image_dir",
                    "source": source,
                    "delay": 0.0,
                    "order": ORDER_BOTTOM_TOP,
                },
            }
        ]
        return App(run_cfg)

    app = build(source_dir)
    try:
        app.service.start_scan()
        wait_scan(app.service)
        rows = app.draft().get("layer1", [])
        fails += check(len(rows) == 2, f"原图检出 2 瓶（实际 {len(rows)}）")
        items = [
            {"index": row["index"], "name": f"回归瓶{row['index']}", "ignore": False}
            for row in rows
        ]
        app.confirm({"layer1": items}, "互换前的基线")
        before = ((app.store.latest_baseline() or {}).get("order") or {}).get("layer1", [])
    finally:
        app.close()

    app = build(swapped)
    try:
        app.service.start_scan()
        wait_scan(app.service)
        view = app.view()
        after = app.store.current_order().get("layer1", [])
        fails += check(
            len(after) == len(before) and sorted(after) == sorted(before) and after != before,
            f"元素相同、顺序不同（互换前 {before} → 互换后 {after}）",
        )
        fails += check(view["diff"] == [], f"不产生差异行（实际 {view['diff']}）")
        fails += check(
            view["order_only"] == {"layer1": True},
            f"标出仅位置不同（实际 {view['order_only']}）",
        )
    finally:
        app.close()
    return fails


def stage_fill_missing(cfg, workdir, source_dir):
    """漏检补位：系统少检出的人能补回来；补位只计数量，不进参考库与位置序列。"""
    print("\n【阶段13】漏检补位（人工兜底）")
    from services.web.server import App

    fails = 0
    work = os.path.join(workdir, "fill")
    os.makedirs(work, exist_ok=True)
    run_cfg = copy.deepcopy(cfg)
    run_cfg["store"] = {"path": os.path.join(work, "app.db"), "data_dir": work}
    run_cfg["vision"] = {"enabled": False, "cache": os.path.join(work, "vision.json")}
    run_cfg["cameras"] = [
        {
            "id": "layer1",
            "name": "回归层",
            "capture": {"backend": "image_dir", "source": source_dir, "delay": 0.0},
        }
    ]
    app = App(run_cfg)
    try:
        app.service.start_scan()
        wait_scan(app.service)
        rows = app.draft().get("layer1", [])
        fails += check(len(rows) == 2, f"系统只检出 2 瓶（实际 {len(rows)}）")

        items = [{"index": row["index"], "name": "回归可乐", "ignore": False} for row in rows]
        items.append({"index": None, "name": "回归可乐", "ignore": False})
        result = app.confirm({"layer1": items}, "补一瓶")
        counts = result["submitted_ids"]["layer1"]
        fails += check(sum(counts.values()) == 3, f"补位后总数 3（实际 {counts}）")
        fails += check(
            result["ingest"]["added"] == 2,
            f"只有检出那两瓶的图入库（实际 {result['ingest']['added']} 张）",
        )
        fails += check(
            result["ingest"]["named"] == 3, f"补位那瓶也算命名（实际 {result['ingest']['named']}）"
        )
        fails += check(
            len(app.store.latest_baseline()["order"]["layer1"]) == 2,
            f"位置序列只含检出的两瓶（实际 {app.store.latest_baseline()['order']['layer1']}）",
        )

        app.service.start_scan()
        wait_scan(app.service)
        pending = app.store.pending_diff()
        target = str(app.store.id_of("回归可乐", create=False))
        fails += check(
            [(row["category"], row["delta"]) for row in pending["diff"]] == [(target, -1)],
            f"漏掉的那瓶在差异里显形（实际 {pending['diff']}）",
        )

        empty = app.confirm(
            {"layer1": [{"index": None, "name": "", "ignore": False}]}, "空名补位"
        )
        fails += check(
            empty["submitted_ids"]["layer1"] == {},
            f"空名的补位不该凭空多出一个（实际 {empty['submitted_ids']}）",
        )
    finally:
        app.close()
    return fails


def stage_vision_live(cfg, workdir, source_dir):
    """真实视觉大模型：同一簇只问一次，候选能回填，同样的图再问不出网。"""
    print("\n【阶段11】视觉大模型真实调用（一簇一问 + 缓存复用）")
    from services.web.server import App

    fails = 0
    work = os.path.join(workdir, "vision")
    os.makedirs(work, exist_ok=True)
    run_cfg = copy.deepcopy(cfg)
    run_cfg["store"] = {"path": os.path.join(work, "app.db"), "data_dir": work}
    run_cfg["vision"] = {
        "enabled": True,
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "",
        "timeout": 30,
        "daily_limit": 50,
        "cache": os.path.join(work, "vision.json"),
        "max_side": 512,
    }
    run_cfg["cameras"] = [
        {
            "id": "layer1",
            "name": "回归层",
            "capture": {"backend": "image_dir", "source": source_dir, "delay": 0.0},
        }
    ]
    app = App(run_cfg)
    try:
        app.service.start_scan()
        wait_scan(app.service)
        rows = app.draft().get("layer1", [])
        clusters = {row["cluster"] for row in rows if not row["known"]}
        fails += check(bool(clusters), f"有未识别目标可分簇（{sorted(clusters)}）")
        started = time.time()
        result = app.suggest()
        elapsed = time.time() - started
        fails += check(
            len(result["items"]) == len(clusters),
            f"一簇只问一次（{len(result['items'])} 条 / {len(clusters)} 簇）",
        )
        fails += check(
            all(isinstance(item["candidate"], str) for item in result["items"]),
            "每条候选都是字符串（猜不出时为空串）",
        )
        fails += check(
            result["calls"] == len(clusters),
            f"网络调用数等于簇数（{result['calls']} / {len(clusters)}）",
        )
        fails += check(elapsed < 90.0, f"整批耗时 {elapsed:.1f}s（有上限）")
        print(f"  候选名: {[item['candidate'] for item in result['items']]}")

        again = app.suggest()
        fails += check(
            again["calls"] == result["calls"],
            f"同样的图再问不出网（仍 {again['calls']} 次）",
        )
    finally:
        app.close()
    return fails


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    parser.add_argument(
        "--live", action="store_true", help="跑真实视觉大模型调用（消耗配额，默认跳过）"
    )
    args = parser.parse_args()

    if not os.path.isdir(GOLDEN):
        print(f"[FAIL] 缺少黄金图目录: {GOLDEN}", file=sys.stderr)
        return 2

    cfg = cfg_mod.load(args.config)
    detector = load_detector(cfg["infer"])
    workdir = tempfile.mkdtemp(prefix="regress-")
    three = make_input(workdir, THREE_BOTTLES, "a")
    two = make_input(workdir, TWO_BOTTLES, "b")
    store = Store(os.path.join(workdir, "app.db"), workdir)
    fails = 0

    try:
        print("\n【阶段1】三瓶场景 → 首次确认建立基线")
        obs1 = observe(cfg, detector, store, three)
        fails += check(obs1 is not None and obs1["count"] == 3, f"检出 3 个（实际 {obs1 and obs1['count']}）")
        fails += check(obs1 is not None and obs1["tier"] == "可信", f"档位可信（实际 {obs1 and obs1['tier']}）")

        reported = store.reported_view()
        fails += check(reported.get("layer1") == {UNK: 3}, f"系统看到 {reported}")

        first = store.confirm({"layer1": {UNK: 3}}, note="建档")
        fails += check(first["initial"], "首次确认标记为建档")
        fails += check(
            [d["delta"] for d in first["baseline_delta"]] == [3],
            f"建档时相对空基线为 +3（实际 {first['baseline_delta']}）",
        )
        baseline = store.latest_baseline()
        fails += check(baseline is not None and baseline["id"] == first["baseline_id"], "基线已建立")

        conf_rows = store.list_confirmations()
        fails += check(
            conf_rows and conf_rows[0]["reported"] == {"layer1": {UNK: 3}},
            "确认记录里存了系统报的全文",
        )
        fails += check(
            conf_rows and conf_rows[0]["submitted"] == {"layer1": {UNK: 3}},
            "确认记录里存了用户提交的全文",
        )

        print("\n【阶段2】换成两瓶场景 → 差异应显示 -1")
        obs2 = observe(cfg, detector, store, two)
        fails += check(obs2 is not None and obs2["count"] == 2, f"检出 2 个（实际 {obs2 and obs2['count']}）")

        pending = store.pending_diff()
        expect = [{"layer": "layer1", "category": UNK, "reported": 3, "submitted": 2, "delta": -1}]
        fails += check(pending["diff"] == expect, f"差异 = {pending['diff']}")
        named = store.resolve_diff(pending["diff"])
        fails += check(
            named and named[0]["category"] == UNKNOWN_CATEGORY and named[0]["category_id"] == UNKNOWN_ID,
            f"差异条目展示时换成名字（实际 {named}）",
        )

        fails += check(len(store.list_events()) == 2, f"事件 2 条（首次 + 净变化），实际 {len(store.list_events())}")

        second = store.confirm({"layer1": {UNK: 2}}, note="少了一瓶")
        fails += check(
            len(second["baseline_delta"]) == 1 and second["baseline_delta"][0]["delta"] == -1,
            f"第二次确认记录了 -1（实际 {second['baseline_delta']}）",
        )
        fails += check(second["corrections"] == [], "用户未修改系统报的结果，纠错为空")
        fails += check(store.pending_diff()["diff"] == [], "确认后差异清零")

        print("\n【阶段3】重复同一场景 → 不应产生新事件（去抖）")
        obs3 = observe(cfg, detector, store, two)
        fails += check(obs3 is not None and obs3["count"] == 2, "检出仍为 2 个")
        fails += check(len(store.list_events()) == 2, f"事件数不变（实际 {len(store.list_events())}）")

        print("\n【阶段4】手动删除历史")
        total = len(store.list_observations())
        newest = store.list_observations(1)[0]
        store.purge(keep_observations=1)
        left = store.list_observations()
        fails += check(
            len(left) == 1 and left[0]["id"] == newest["id"],
            f"保留最近 1 条观测（原有 {total}，留下 {[o['id'] for o in left]}）",
        )
        fails += check(store.list_events() == [], "事件已清空")
        fails += check(store.list_confirmations() == [], "确认记录已清空")
        fails += check(store.latest_scan(SCAN_DONE) is None, "扫描记录已清空")
        fails += check(
            store.current_observations() == [], "当前状态跟着清空（保留的观测只是历史证据）"
        )
        fails += check(store.latest_baseline() is not None, "基线保留")
        folder = os.path.join(workdir, "observations")
        dirs = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        fails += check(
            dirs == [str(newest["id"])], f"只留下这条观测的图片目录（实际 {dirs}）"
        )

        store.purge()
        fails += check(store.list_observations() == [], "观测已清空")
        fails += check(store.list_events() == [], "事件已清空")
        fails += check(store.latest_baseline() is not None, "基线保留")
        fails += check(
            not os.path.isdir(folder) or os.listdir(folder) == [], "观测图片已删除"
        )

        print("\n【阶段5】导出包结构")
        bundle = store.export_bundle()
        fails += check(
            set(bundle) >= {"exported_at", "categories", "baseline", "observations", "confirmations", "events"},
            f"导出字段齐全: {sorted(bundle)}",
        )

        print("\n【阶段6】命名建档 / 改名 / 合并 / 删除")
        coke = store.get_or_create_category("可口可乐")
        coconut = store.get_or_create_category("椰子水")
        named = store.confirm({"layer1": {str(coke): 2, str(coconut): 1}}, note="命名建档")
        fails += check(
            coke is not None and coconut is not None and coke != coconut, "未识别改成具名后建了两个类别"
        )
        fails += check(
            named["submitted_ids"] == {"layer1": {str(coke): 2, str(coconut): 1}},
            f"落库键是类别 id（实际 {named['submitted_ids']}）",
        )

        store.rename_category(coke, "零度可乐")
        fails += check(
            store.resolve(store.latest_baseline()["payload"]) == {"layer1": {"零度可乐": 2, "椰子水": 1}},
            "改名后历史随访新名字",
        )
        fails += check(
            store.categories() and "可口可乐" not in store.categories(), "旧名字不再出现在类别清单"
        )

        existing = store.reference_groups()
        dim = len(existing[0]["vector"]) if existing else 2
        probe = np.zeros(dim, dtype=np.float32)
        probe[0] = 1.0

        store.add_reference_by_id(coconut, probe, "fake.jpg", "confirm")
        store.merge_categories(coconut, coke)
        after = [g for g in store.reference_groups() if g["category_id"] == coke]
        fails += check(len(after) == 1, f"合并后参考图改指向目标类别（实际 {len(after)}）")
        fails += check(store.merge_map() == {coconut: coke}, "合并日志已写（旧 id → 新 id）")
        fails += check(store.id_of("椰子水", create=False) is None, "合并后源类别行已删除")
        fails += check("椰子水" not in store.categories(), "源类别不再出现在类别清单")
        fails += check(
            store.resolve({"layer1": {str(coconut): 1}}) == {"layer1": {"零度可乐": 1}},
            "历史里的旧 id 折叠成目标名字",
        )

        doomed = store.get_or_create_category("待删类别")
        store.add_reference_by_id(doomed, probe, "fake2.jpg", "test")
        fails += check(
            any(g["category_id"] == doomed for g in store.reference_groups()),
            "删除前该类别参与识别",
        )
        store.delete_category(doomed)
        fails += check(
            not any(g["category_id"] == doomed for g in store.reference_groups()),
            "删除时参考图一并清掉",
        )
        fails += check(store.id_of("待删类别", create=False) is None, "删除后类别行不存在")
        fails += check(
            store.resolve({"layer1": {str(doomed): 1}}) == {"layer1": {UNKNOWN_CATEGORY: 1}},
            "引用已删类别的历史显示为未识别",
        )

        fails += stage_offline(cfg, detector, workdir, three)
        fails += stage_scan(cfg, detector, workdir, three, two)
        fails += stage_order(cfg, detector, workdir, three)
        fails += stage_bottles(cfg, workdir, make_input(workdir, MIXED, "c"))
        if args.live:
            fails += stage_vision_live(cfg, workdir, three)
        else:
            print("\n【阶段11】视觉大模型真实调用 —— 跳过（加 --live 才跑，会消耗配额）")
        fails += stage_order_swap(cfg, workdir, make_input(workdir, MIXED, "d"), detector)
        fails += stage_fill_missing(cfg, workdir, make_input(workdir, MIXED, "e"))
    finally:
        store.conn.close()
        detector.close()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'[ OK ] 回归通过' if fails == 0 else f'[FAIL] {fails} 项不通过'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
