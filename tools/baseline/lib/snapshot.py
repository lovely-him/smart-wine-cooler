#!/usr/bin/env python3
"""接口回归快照：同一串请求跑一遍，与入库的 golden.json 比对。

它只回答「返回和上次记的一样吗」。变了不等于坏了——好不好由人看 diff 决定。
有意改接口时跑 `update` 刷新 golden。

用法: snapshot.py <仓库根> <golden.json> <运行目录> [--update]
退出码: 0 一致 / 1 有差异 / 2 参数或环境问题
"""

import difflib
import json
import os
import shutil
import sys
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:
    print(
        "[FAIL] 缺少 fastapi 或 httpx2，请用宿主环境跑：server/.venv/bin/python",
        file=sys.stderr,
    )
    sys.exit(2)

# ── 路径与常量 ──
# 环境相关、每次都不一样的字段：抹掉值但保留键，形状照样对得上
VOLATILE_KEYS = {
    "at",
    "startedAt",
    "finishedAt",
    "createdAt",
    "updatedAt",
    "hostUptimeSeconds",
    "serviceUptimeSeconds",
    "freeBytes",
    "totalBytes",
    "storageFreeBytes",
    "storageTotalBytes",
    "storageLow",
    "ip",
    "now",
    "ssid",
    "address",
    "hostname",
    "mdnsUrl",
    "available",
}
CASES = [
    ("snapshot-empty", "GET", "/api/snapshot", None),
    ("history-empty", "GET", "/api/history", None),
    ("history-page", "GET", "/api/history?page=1&size=2", None),
    ("observations", "GET", "/api/observations", None),
    ("changes", "GET", "/api/changes", None),
    ("fields", "GET", "/api/fields", None),
    ("ai-status", "GET", "/api/ai/status", None),
    ("logs", "GET", "/api/logs", None),
    ("cameras", "GET", "/api/cameras", None),
    ("layers", "GET", "/api/layers", None),
    ("settings", "GET", "/api/settings", None),
    ("network", "GET", "/api/network", None),
    (
        "category-create",
        "POST",
        "/api/categories",
        {"name": "基线条目", "shape": "bordeaux", "details": {"brand": "基线"}},
    ),
    ("category-usage", "GET", "/api/categories/1/usage", None),
    ("snapshot-after-category", "GET", "/api/snapshot", None),
    (
        "field-create",
        "POST",
        "/api/fields",
        {"key": "probe", "label": "探针", "askable": True, "categoryId": None},
    ),
    ("fields-after-create", "GET", "/api/fields", None),
    ("category-usage-404", "GET", "/api/categories/99999/usage", None),
    ("category-create-422", "POST", "/api/categories", {}),
    ("light-on", "PUT", "/api/light", {"on": True}),
    ("snapshot-after-light", "GET", "/api/snapshot", None),
    ("light-off", "PUT", "/api/light", {"on": False}),
    ("fields-delete", "DELETE", "/api/fields/1", None),
    ("fields-after-delete", "GET", "/api/fields", None),
]
DIFF_LINES = 40


# ── 函数 ──
def scrub(value, key=""):
    if isinstance(value, dict):
        return {k: scrub(item, k) for k, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if key in VOLATILE_KEYS:
        return "<volatile>"
    return value


def body_of(response):
    kind = response.headers.get("content-type", "")
    if not response.content:
        return "<empty>"
    if "json" not in kind:
        return f"<{kind or 'no content-type'}: {len(response.content)} bytes>"
    try:
        return scrub(response.json())
    except ValueError:
        return f"<json 解码失败: {response.text[:200]}>"


def collect(client):
    records = []
    for name, method, path, payload in CASES:
        response = client.request(method, path, json=payload)
        records.append(
            {
                "case": name,
                "method": method,
                "path": path,
                "status": response.status_code,
                "body": body_of(response),
            }
        )
    return records


def render(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def diff_text(expected, actual):
    lines = list(
        difflib.unified_diff(
            render(expected).splitlines(),
            render(actual).splitlines(),
            fromfile="golden",
            tofile="现在",
            lineterm="",
            n=2,
        )
    )
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [f"……还有 {len(lines) - DIFF_LINES} 行"]
    return "\n".join("    " + line for line in lines)


def compare(expected, actual):
    problems = []
    old = {item["case"]: item for item in expected}
    new = {item["case"]: item for item in actual}
    for name in sorted(old.keys() - new.keys()):
        problems.append(f"{name}: golden 里有，这次没跑（用例被删了？）")
    for name in sorted(new.keys() - old.keys()):
        problems.append(f"{name}: 这次新增，golden 里没有")
    for name in sorted(old.keys() & new.keys()):
        if old[name]["status"] != new[name]["status"]:
            problems.append(
                f"{name}: 状态码 {old[name]['status']} → {new[name]['status']}"
            )
        if render(old[name]["body"]) != render(new[name]["body"]):
            problems.append(
                f"{name}: 响应体不同\n{diff_text(old[name]['body'], new[name]['body'])}"
            )
    return problems


def snapshot(root, run_dir):
    """用全新临时库跑一遍，主键与序列号因此是确定的。"""
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    os.environ["DATA_DIR"] = str(run_dir)
    sys.path.insert(0, str(root))

    from server.app import create_app

    return collect(TestClient(create_app(start_scheduler=False)))


def main(argv):
    if len(argv) not in (4, 5):
        print("用法: snapshot.py <仓库根> <golden.json> <运行目录> [--update]", file=sys.stderr)
        return 2

    root = Path(argv[1]).resolve()
    golden = Path(argv[2])
    run_dir = Path(argv[3])
    update = len(argv) == 5 and argv[4] == "--update"

    actual = snapshot(root, run_dir)

    if update:
        text = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        golden.write_text(text, encoding="utf-8")
        print(f"  已刷新 {golden.relative_to(root)}（{len(actual)} 个用例）")
        return 0

    if not golden.is_file():
        print(f"[FAIL] 没有基线文件 {golden}", file=sys.stderr)
        print("       第一次使用先采集：tools/baseline/run.sh update", file=sys.stderr)
        return 2

    expected = json.loads(golden.read_text(encoding="utf-8"))
    problems = compare(expected, actual)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(
            f"\n[FAIL] {len(problems)} 处与 golden 不一致"
            "（确认是有意改接口就跑 tools/baseline/run.sh update）",
            file=sys.stderr,
        )
        return 1
    print(f"  {len(actual)} 个用例，与 golden 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
