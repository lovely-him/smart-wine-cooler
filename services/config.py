"""配置加载：一个 app.json 描述推理后端与各层摄像头，两端共用同一份结构。

配置里的相对路径一律以**仓库根目录**为基准，这样宿主与板卡可以共用同一份写法（各自的绝对前缀不同）。
"""

import json
import os
import sys

# ── 路径与常量 ──
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "config", "app.json")

# ── 函数 ──
def resolve(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def load(path=DEFAULT_PATH):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"配置文件不存在: {path}（可从 config/app.example.json 复制）")
    with open(path, encoding="utf-8") as fp:
        cfg = json.load(fp)

    infer = cfg.get("infer") or {}
    if not infer.get("backend"):
        raise ValueError("配置缺少 infer.backend")
    if not infer.get("model"):
        raise ValueError("配置缺少 infer.model")
    infer["model"] = resolve(infer["model"])

    cameras = cfg.get("cameras") or []
    if not cameras:
        raise ValueError("配置里没有任何摄像头")
    for cam in cameras:
        capture = cam.get("capture") or {}
        if not capture.get("backend"):
            raise ValueError(f"摄像头 {cam.get('id')} 缺少 capture.backend")
        if capture["backend"] == "image_dir":
            capture["source"] = resolve(capture["source"])

    store = cfg.get("store") or {}
    store.setdefault("path", "data/app.db")
    store.setdefault("data_dir", "data")
    store["path"] = resolve(store["path"])
    store["data_dir"] = resolve(store["data_dir"])
    cfg["store"] = store

    web = cfg.get("web") or {}
    web.setdefault("host", "127.0.0.1")
    web.setdefault("port", 8080)
    cfg["web"] = web

    vision = cfg.get("vision")
    if vision:
        vision.setdefault("enabled", False)
        vision.setdefault("cache", "cache/vision-cache.json")
        vision["cache"] = resolve(vision["cache"])
        cfg["vision"] = vision

    cfg["export"] = cfg.get("export") or {}
    return cfg


def camera(cfg, camera_id):
    for cam in cfg["cameras"]:
        if cam["id"] == camera_id:
            return cam
    names = ", ".join(c["id"] for c in cfg["cameras"])
    raise ValueError(f"没有这个摄像头: {camera_id}（可选: {names}）")


# ── main ──
def main():
    try:
        cfg = load(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"推理后端: {cfg['infer']['backend']}  模型: {cfg['infer']['model']}")
    for cam in cfg["cameras"]:
        cap = cam["capture"]
        where = cap.get("source") or cap.get("device")
        print(f"  {cam['id']:8s} {cam['name']:16s} {cap['backend']:10s} {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
