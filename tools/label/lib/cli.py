"""批量标注：本地聚类 → 视觉大模型给候选名 → 人工确认 → 写进参考库。

为什么要先聚类：同一款可乐被检出 3 瓶，三次调用可能得到「可口可乐」「可乐」「Coca-Cola」，
会建成三个类别。同簇只问一次，名字给整簇用。

labels.json 字段：
    file       裁剪图路径
    cluster    特征聚类编号（-1 = 已被参考库直接认出，不占用调用）
    candidate  大模型给的候选名（草稿）
    category   用户改定的名字（apply 只认这个）
    confirmed  用户勾选（apply 只写 true）
    note       备注/跳过原因
"""

import argparse
import json
import os
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # 依赖缺失不走 traceback
    print(f"缺少依赖: {exc.name}", file=sys.stderr)
    print("安装: tools/.venv/bin/pip install -r tools/label/requirements.txt", file=sys.stderr)
    sys.exit(2)

import services.config as cfg_mod
from services.classify import load_classifier
from services.classify.cluster import cluster_vectors
from services.infer import load_embedder
from services.store.repo import Store, UNKNOWN_ID
from services.vision import load_vision_or_noop

# ── 路径与常量 ──
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_OUT = "tmp/labels.json"
DEFAULT_ALIASES = "tools/label/aliases.json"
IMAGE_EXT = (".jpg", ".jpeg", ".png")


# ── 函数 ──
def list_images(folder):
    found = []
    for base, _, names in os.walk(folder):
        for name in sorted(names):
            if name.lower().endswith(IMAGE_EXT):
                found.append(os.path.join(base, name))
    return sorted(found)


def build_tools(cfg):
    store = Store(cfg["store"]["path"], cfg["store"]["data_dir"])
    embedder = load_embedder(cfg["embed"])
    classifier = load_classifier(cfg["classify"], store, embedder)
    return store, embedder, classifier


def cmd_scan(args):
    cfg = cfg_mod.load(args.config)
    vision_cfg = cfg.get("vision", {})
    threshold = args.cluster
    if threshold is None:
        threshold = float(vision_cfg.get("cluster_threshold", 0.80))
    store, embedder, classifier = build_tools(cfg)
    vision = load_vision_or_noop(
        vision_cfg, ROOT, allow_network=not args.no_network
    )
    before_calls = vision.calls

    files = list_images(args.crops)
    if not files:
        print(f"[FAIL] {args.crops} 下没有图片", file=sys.stderr)
        return 2

    items, unknown_vecs, unknown_files = [], [], []
    for path in files:
        with Image.open(path) as fp:
            image = fp.convert("RGB")
        verdict = classifier.classify(image)
        if verdict["category_id"] not in (None, UNKNOWN_ID):
            items.append({
                "file": path, "cluster": -1, "candidate": verdict["category"],
                "category": verdict["category"], "confirmed": False,
                "note": f"参考库已认出（相似度 {verdict.get('best_score') or 0:.3f}）",
            })
        else:
            unknown_vecs.append(verdict["feature"])
            unknown_files.append(path)

    groups, centroids = cluster_vectors(unknown_vecs, threshold) if unknown_vecs else ([], [])
    picked = 0
    for slot, member_indexes in enumerate(groups):
        vectors = np.stack([unknown_vecs[i] for i in member_indexes])
        centroid = centroids[slot]
        representative = int(member_indexes[int(np.argmax(vectors @ centroid))])
        with Image.open(unknown_files[representative]) as fp:
            image = fp.convert("RGB")
        result = vision.describe(image)
        picked += 1 if result else 0
        for i in member_indexes:
            items.append({
                "file": unknown_files[i], "cluster": slot,
                "candidate": (result or {}).get("name", ""),
                "category": (result or {}).get("name", ""),
                "confirmed": False,
                "note": "" if result else "未获得候选名（无凭据/超限/失败）",
            })

    payload = {
        "source": vision.provider.name,
        "cluster_threshold": threshold,
        "items": items,
    }
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    print(f"图片 {len(items)} 张 | 参考库已认出 {len(items) - len(unknown_files)} 张 | "
          f"待定 {len(unknown_files)} 张 → {len(groups)} 簇")
    print(f"拿到候选 {picked} 条 | 实际调用 {vision.calls - before_calls} 次 | "
          f"缓存命中 {vision.hits} | 来源 {vision.provider.name}")
    if vision.blocked:
        print("[WARN] 已达每日调用上限，剩余簇未问大模型")
    print(f"候选表已写入 {out}")
    print(f"下一步: 编辑该文件（填 category、置 confirmed=true），再跑 table / apply")
    store.conn.close()
    embedder.close()
    vision.close()
    return 0


def cmd_table(args):
    path = args.input if os.path.isabs(args.input) else os.path.join(ROOT, args.input)
    with open(path, encoding="utf-8") as fp:
        payload = json.load(fp)
    items = payload.get("items", [])
    print(f"来源 {payload.get('source')} | 共 {len(items)} 条\n")
    print(f"{'簇':>3} {'确认':^4} {'候选名':<16} {'目录':<28} 文件")
    for item in items:
        folder = os.path.basename(os.path.dirname(item["file"]))
        mark = "√" if item.get("confirmed") else "-"
        name = item.get("category") or "(空)"
        print(f"{item.get('cluster', -1):>3} {mark:^4} {name:<16} {folder:<28} {os.path.basename(item['file'])}")
    done = sum(1 for i in items if i.get("confirmed") and i.get("category"))
    print(f"\n已确认可入库 {done} 条")
    return 0


def load_aliases(path):
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    mapping = {}
    try:
        with open(full, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return mapping
    for canonical, aliases in data.items():
        mapping[canonical] = canonical
        for alias in aliases:
            mapping[alias] = canonical
    return mapping


def cmd_apply(args):
    cfg = cfg_mod.load(args.config)
    path = args.input if os.path.isabs(args.input) else os.path.join(ROOT, args.input)
    with open(path, encoding="utf-8") as fp:
        payload = json.load(fp)

    aliases = load_aliases(args.aliases)
    store, embedder, classifier = build_tools(cfg)

    added, dup, skipped, categories = 0, 0, 0, {}
    for item in payload.get("items", []):
        name = (item.get("category") or "").strip()
        if not item.get("confirmed") or not name:
            skipped += 1
            continue
        canonical = aliases.get(name, name)
        category_id = store.get_or_create_category(canonical)
        with Image.open(item["file"]) as fp:
            image = fp.convert("RGB")
        vector = classifier.feature(image)
        ok, why = classifier.add_reference(category_id, vector, item["file"], "label", 1.0)
        if ok:
            added += 1
            categories[canonical] = categories.get(canonical, 0) + 1
        else:
            dup += 1
            item["note"] = why

    print(f"入库 {added} 张 | 跳过（未确认/空名）{skipped} | 去重或超配额 {dup}")
    for name, count in sorted(categories.items()):
        print(f"  {name}: +{count}")
    if args.save_notes:
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        print(f"跳过原因已回写到 {path}")
    store.conn.close()
    embedder.close()
    return 0


def cmd_selftest(args):
    """不联网、不碰真实库：空候选、0 调用、apply 全跳过都要跑得通。"""
    import copy
    import shutil
    import tempfile

    base = cfg_mod.load(args.config)
    work = tempfile.mkdtemp(prefix="label-selftest-")
    crops = args.crops or os.path.join(ROOT, "tools/embed_lab/data/crop")
    fails = 0

    def check(ok, message):
        nonlocal fails
        print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
        fails += 0 if ok else 1

    try:
        cfg = copy.deepcopy(base)
        cfg["store"] = {"path": os.path.join(work, "app.db"), "data_dir": work}
        cfg["vision"] = dict(cfg.get("vision", {}))
        cfg["vision"]["cache"] = os.path.join(work, "vision-cache.json")
        cfg_path = os.path.join(work, "app.json")
        with open(cfg_path, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False)
        out = os.path.join(work, "labels.json")

        print("\n【自检】无网络路径")
        code = cmd_scan(argparse.Namespace(
            crops=crops, out=out, config=cfg_path, cluster=None, no_network=True))
        check(code == 0, f"scan 退出码 0（实际 {code}）")
        with open(out, encoding="utf-8") as fp:
            payload = json.load(fp)
        empty = [i for i in payload["items"] if i.get("candidate")]
        check(not empty, f"全部为空候选（有名字的 {len(empty)} 条）")
        check(payload["source"] == "noop", f"来源为 noop（实际 {payload['source']}）")
        check(not os.path.exists(os.path.join(work, "vision-cache.json")), "没有写缓存文件")

        code = cmd_apply(argparse.Namespace(
            input=out, config=cfg_path, aliases=DEFAULT_ALIASES, save_notes=False))
        check(code == 0, f"apply 退出码 0（未确认条目全跳过）")

        store = Store(os.path.join(work, "app.db"), work)
        check(store.reference_count() == 0, f"参考库仍为空（实际 {store.reference_count()}）")
        store.conn.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{'[ OK ] 自检通过' if fails == 0 else f'[FAIL] {fails} 项不通过'}")
    return 0 if fails == 0 else 1


# ── main ──
def main():
    parser = argparse.ArgumentParser(description="批量标注（聚类 + 视觉大模型候选名）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="扫描裁剪图，产出候选表")
    scan.add_argument("--crops", required=True)
    scan.add_argument("--out", default=DEFAULT_OUT)
    scan.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    scan.add_argument("--cluster", type=float, default=None)
    scan.add_argument("--no-network", action="store_true", help="不联网，只跑聚类与本地识别")
    scan.set_defaults(func=cmd_scan)

    table = sub.add_parser("table", help="打印候选表")
    table.add_argument("--in", dest="input", default=DEFAULT_OUT)
    table.set_defaults(func=cmd_table)

    apply_cmd = sub.add_parser("apply", help="把已确认的条目写进参考库")
    apply_cmd.add_argument("--in", dest="input", default=DEFAULT_OUT)
    apply_cmd.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    apply_cmd.add_argument("--aliases", default=DEFAULT_ALIASES)
    apply_cmd.add_argument("--save-notes", action="store_true", help="把跳过原因回写到候选表")
    apply_cmd.set_defaults(func=cmd_apply)

    selftest = sub.add_parser("selftest", help="离线自检（不联网、不碰真实库）")
    selftest.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    selftest.add_argument("--crops", default="")
    selftest.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
