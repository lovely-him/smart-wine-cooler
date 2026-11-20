"""S7a 验收脚本：整柜扫描的四个场景（目录回放，不接硬件）。

用法:
    python -m services.pipeline.demo [--config config/app.json]

四个场景对应 docs/inventory-loop.md §3.2：
1. 正常盘点 → 所有层完成，扫描状态 done
2. 盘点中再次开始 → 前一次作废，只提交后一次的结果
3. 盘点中停止 → 扫描作废，不产生观测
4. 多层串行 → 同一时刻只跑一层，不交叉
"""

import argparse
import sys
import time

from services import config as cfg_mod
from services.infer import load_detector
from services.pipeline.runner import (
    STATE_ABORTED,
    STATE_DONE,
    STATE_ERROR,
    STATE_MEASURING,
    STATE_SETTLING,
    PipelineService,
)

# ── 路径与常量 ──
WAIT = 60.0
RUNNING = (STATE_SETTLING, STATE_MEASURING)


# ── 函数 ──
def reset(service):
    service.trace.clear()
    for runner in service.runners.values():
        with runner.lock:
            runner.state = "idle"
            runner.observation = None
            runner.error = ""


def report(service, title):
    print(f"\n【{title}】")
    for item in service.status():
        obs = item["observation"]
        desc = "-" if not obs else f"{obs['tier']} 检出 {obs['count']} 个"
        print(f"  {item['id']:8s} state={item['state']:9s} epoch={item['epoch']} 观测: {desc}")


def check(ok, message):
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    return 0 if ok else 1


def wait_scan(service, timeout=WAIT):
    service.wait(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = service.scan_snapshot()
        if snap["state"] in ("done", "failed", "aborted"):
            return snap
        time.sleep(0.02)
    return service.scan_snapshot()


def scenario_normal(service):
    reset(service)
    service.start_scan()
    snap = wait_scan(service)
    report(service, "场景1 正常盘点")

    print(f"  扫描: #{snap['id']} {snap['state']} {snap['finished']}")
    fails = 0
    fails += check(snap["state"] == "done", f"扫描完成（实际 {snap['state']}）")
    fails += check(
        all(item["state"] == STATE_DONE for item in service.status()), "所有层都到 done"
    )
    layer1 = service.runners["layer1"].snapshot()["observation"]
    fails += check(layer1 is not None, "layer1 产生了观测")
    fails += check(layer1 is not None and layer1["tier"] in ("可信", "存疑"), "档位落在可信/存疑")
    return fails


def scenario_restart(service):
    reset(service)
    first = service.start_scan()
    time.sleep(0.08)
    second = service.start_scan()
    snap = wait_scan(service)
    report(service, "场景2 盘点中再次开始（重启）")

    print(f"  扫描: #{first['scan_id']} → #{second['scan_id']}，最终 {snap['state']}")
    done_events = [t for t in service.trace if t[1] == "scan" and t[2] == "done"]
    fails = 0
    fails += check(second["token"] != first["token"], "开始了新的一次盘点")
    fails += check(snap["state"] == "done", f"最终扫描完成（实际 {snap['state']}）")
    fails += check(len(done_events) == 1, f"只有一次扫描完成（实际 {len(done_events)}）")
    return fails


def scenario_abort(service):
    reset(service)
    service.start_scan()
    time.sleep(0.08)
    service.abort_scan()
    snap = wait_scan(service)
    report(service, "场景3 盘点中停止")

    fails = 0
    fails += check(snap["state"] == "aborted", f"扫描作废（实际 {snap['state']}）")
    fails += check(
        all(item["observation"] is None or item["state"] == STATE_ABORTED
            for item in service.status()),
        "没有提交任何层的观测",
    )
    return fails


def scenario_serial(service):
    reset(service)
    service.start_scan()
    snap = wait_scan(service)
    report(service, "场景4 多层串行")

    busy = None
    crossed = 0
    for _, cam, state, _ in service.trace:
        if cam == "scan":
            continue
        if state in RUNNING:
            if busy is None:
                busy = cam
            elif busy != cam:
                crossed += 1
        elif state in (STATE_DONE, STATE_ABORTED, STATE_ERROR) and busy == cam:
            busy = None

    fails = 0
    fails += check(snap["state"] == "done", f"一次扫描内所有层完成（实际 {snap['state']}）")
    fails += check(crossed == 0, f"执行未交叉（交叉次数 {crossed}）")
    return fails


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    args = parser.parse_args()

    try:
        cfg = cfg_mod.load(args.config)
        detector = load_detector(cfg["infer"])
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    service = PipelineService(cfg, detector)
    try:
        fails = 0
        fails += scenario_normal(service)
        fails += scenario_restart(service)
        fails += scenario_abort(service)
        fails += scenario_serial(service)
    finally:
        service.shutdown()
        detector.close()

    print(f"\n{'[ OK ] 全部通过' if fails == 0 else f'[FAIL] {fails} 项不通过'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
