"""自检实验环境：依赖、模型输入输出、数据目录与 manifest 一致性。"""

import argparse
import json
import os
import sys

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
SPLITS = ("ref", "query", "neg", "crop")


# ── 函数 ──
def list_images(path):
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path) if f.lower().endswith(EXTS))


def report_runtime():
    import numpy
    import onnxruntime
    import PIL

    print(f"python      : {sys.version.split()[0]}")
    print(f"numpy       : {numpy.__version__}")
    print(f"onnxruntime : {onnxruntime.__version__}")
    print(f"pillow      : {PIL.__version__}")
    print(f"providers   : {onnxruntime.get_available_providers()}")


def report_model(path):
    import onnxruntime

    if not os.path.isfile(path):
        print(f"model       : 缺失 {path}")
        return False
    print(f"model       : {path} ({os.path.getsize(path)} bytes)")
    session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
    for spec in session.get_inputs():
        print(f"  input     : {spec.name} {spec.shape}")
    for spec in session.get_outputs():
        print(f"  output    : {spec.name} {spec.shape}")
    return True


def load_manifest(path, root):
    if not os.path.isfile(path):
        return None
    rows = []
    bad = 0
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            row["exists"] = os.path.isfile(os.path.join(root, row.get("path", "")))
            rows.append(row)
    return rows, bad


def report_data(root, manifest_path):
    loaded = load_manifest(manifest_path, root)
    if loaded is None:
        print(f"{'manifest':11s} : 缺失 {manifest_path}")
        rows = []
        bad = 0
    else:
        rows, bad = loaded
        print(f"{'manifest':11s} : {len(rows)} 条（非法 {bad} 条）")

    total_missing = sum(0 if r["exists"] else 1 for r in rows)
    if total_missing:
        print(f"{'':11s}   其中 {total_missing} 条指向的文件不存在")

    for split in SPLITS:
        on_disk = list_images(os.path.join(root, split))
        registered = {os.path.basename(r.get("path", "")) for r in rows if r.get("split") == split}
        unregistered = [f for f in on_disk if f not in registered]
        labels = {}
        labeled_query = 0
        for row in rows:
            if row.get("split") != split:
                continue
            label = row.get("label") or ""
            if label:
                labels[label] = labels.get(label, 0) + 1
                if split == "query":
                    labeled_query += 1
        detail = " ".join(f"{k}={v}" for k, v in sorted(labels.items())) or "-"
        line = f"{split:11s} : {len(on_disk)} 张，manifest {len(registered)} 条，类别 {detail}"
        if split == "query":
            line += f"，有类标 {labeled_query}"
        if unregistered:
            line += f"，未登记 {len(unregistered)} 张"
        print(line)

    return sum(len(list_images(os.path.join(root, s))) for s in SPLITS)


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--manifest", default="")
    args = parser.parse_args()

    report_runtime()
    has_model = report_model(args.model)
    total = report_data(args.data, args.manifest or os.path.join(args.data, "manifest.jsonl"))

    if not has_model:
        print("\n[FAIL] 缺少模型，执行: ./tools/embed_lab/run.sh setup")
        return 1
    if total == 0:
        print("\n[WARN] 无数据。先跑 fetch-ref / detect。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
