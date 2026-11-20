"""评估外观特征的检索能力：输出 top-1、相似度分布与阈值建议。

数据一律从 manifest.jsonl 读取（图片目录只按用途分层，类别等语义写在 manifest 里）：
    ref    参考图（原型库），必须有 label
    query  查询图，有 label 的参与 top-1，无 label 的只统计
    neg    负样本
"""

import argparse
import json
import os
import sys

try:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
except ImportError as exc:
    print(f"[FAIL] 缺少依赖 {exc.name}，执行: ./tools/embed_lab/run.sh setup", file=sys.stderr)
    sys.exit(1)

# ── 路径与常量 ──
SIZE = 224
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
NEG_CLASS = "__neg__"
USED_SPLITS = ("ref", "query", "neg")


# ── 函数 ──
def load_manifest(path, data_root):
    if not os.path.isfile(path):
        print(f"[FAIL] manifest 不存在: {path}（先用 fetch-ref / detect 生成）", file=sys.stderr)
        return None
    items = []
    with open(path, encoding="utf-8") as fp:
        for lineno, line in enumerate(fp, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARN] manifest 第 {lineno} 行不是合法 JSON，跳过", file=sys.stderr)
                continue
            if row.get("split") not in USED_SPLITS:
                continue
            full = os.path.join(data_root, row.get("path", ""))
            if not full.lower().endswith(EXTS) or not os.path.isfile(full):
                continue
            row["full"] = full
            items.append(row)
    return items


def load_session(path):
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, os.cpu_count() or 1)
    return ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])


def preprocess(path):
    with Image.open(path) as img:
        img = img.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
    return ((arr - MEAN) / STD).transpose(2, 0, 1)[None]


def embed_all(session, files, out_name):
    name = session.get_inputs()[0].name
    vecs = []
    for path in files:
        try:
            out = session.run([out_name], {name: preprocess(path)})[0]
        except Exception as exc:
            print(f"[WARN] 跳过 {path}: {exc}", file=sys.stderr)
            vecs.append(None)
            continue
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        vecs.append(vec / norm if norm else vec)
    return vecs


def evaluate(refs, queries, exclude_self):
    if not refs or not queries:
        return None
    ref_mat = np.stack([vec for _, _, vec in refs])
    query_mat = np.stack([vec for _, _, vec in queries])
    sims = query_mat @ ref_mat.T

    if exclude_self:
        ref_paths = [path for _, path, _ in refs]
        for i, (_, qpath, _) in enumerate(queries):
            for j, rpath in enumerate(ref_paths):
                if rpath == qpath:
                    sims[i, j] = -1.0

    classes = sorted({cls for cls, _, _ in refs})
    pos, neg, margins = [], [], []
    correct = total = 0
    confusion = {cls: {} for cls in classes}
    per_class = {cls: [0, 0] for cls in classes}

    for i, (cls, _, _) in enumerate(queries):
        best = {c: -1.0 for c in classes}
        for j, (ref_cls, _, _) in enumerate(refs):
            if sims[i, j] > best[ref_cls]:
                best[ref_cls] = sims[i, j]
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)

        if cls == NEG_CLASS or cls not in classes:
            neg.append(float(ranked[0][1]))
            continue

        total += 1
        top1_cls, top1_sim = ranked[0]
        top2_sim = ranked[1][1] if len(ranked) > 1 else -1.0
        pos.append(float(best[cls]))
        wrong = [s for c, s in best.items() if c != cls and s >= 0]
        if wrong:
            neg.append(float(max(wrong)))
        per_class[cls][1] += 1
        confusion[cls][top1_cls] = confusion[cls].get(top1_cls, 0) + 1
        if top1_cls == cls:
            correct += 1
            per_class[cls][0] += 1
            if len(ranked) > 1:
                margins.append(float(top1_sim - top2_sim))

    return {
        "n_query": total,
        "n_ref": len(refs),
        "classes": classes,
        "top1": (correct / total) if total else 0.0,
        "per_class": {c: (v[0] / v[1] if v[1] else 0.0) for c, v in per_class.items()},
        "confusion": confusion,
        "pos": pos,
        "neg": neg,
        "margins": margins,
    }


def stats(values):
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float32)
    return {
        "n": int(arr.size),
        "mean": round(float(arr.mean()), 4),
        "p05": round(float(np.percentile(arr, 5)), 4),
        "p50": round(float(np.percentile(arr, 50)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
    }


def suggest_tau(pos, neg):
    if not pos or not neg:
        return None
    best_tau, best_j = 0.0, -2.0
    for tau in np.linspace(0.05, 0.995, 190):
        tpr = float(np.mean(np.asarray(pos) >= tau))
        fpr = float(np.mean(np.asarray(neg) >= tau))
        if tpr - fpr > best_j:
            best_tau, best_j = float(tau), tpr - fpr
    return {"tau": round(best_tau, 3), "youden": round(best_j, 3)}


def print_result(name, res):
    if res is None:
        print(f"\n[{name}] 跳过：数据不足")
        return
    print(f"\n[{name}] refs={res['n_ref']} queries={res['n_query']} top1={res['top1']:.3f}")
    print(f"  每类命中   {res['per_class']}")
    print(f"  混淆矩阵   {res['confusion']}")
    print(f"  正样本相似度 {stats(res['pos'])}")
    print(f"  负样本相似度 {stats(res['neg'])}")
    print(f"  类间间隔     {stats(res['margins'])}")


def collect(rows, session, out_name):
    splits = {name: [r for r in rows if r["split"] == name] for name in USED_SPLITS}
    refs = []
    for row in splits["ref"]:
        if not row.get("label"):
            continue
        vec = embed_all(session, [row["full"]], out_name)[0]
        if vec is not None:
            refs.append((row["label"], row["full"], vec))
    queries = []
    for row in splits["query"]:
        vec = embed_all(session, [row["full"]], out_name)[0]
        if vec is not None:
            queries.append((row.get("label") or NEG_CLASS, row["full"], vec))
    negs = []
    for row in splits["neg"]:
        vec = embed_all(session, [row["full"]], out_name)[0]
        if vec is not None:
            negs.append((NEG_CLASS, row["full"], vec))
    return refs, queries, negs, splits


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    manifest = args.manifest or os.path.join(args.data, "manifest.jsonl")
    rows = load_manifest(manifest, args.data)
    if rows is None:
        return 2

    pool_model = os.path.join(os.path.dirname(os.path.abspath(args.model)), "mobilenetv2_pool.onnx")
    model_path = pool_model if os.path.isfile(pool_model) else args.model
    print(f"model   : {model_path}")

    session = load_session(model_path)
    outputs = session.get_outputs()
    out_name = outputs[-1].name if len(outputs) > 1 else outputs[0].name
    print(f"feature : {out_name} ({len(outputs)} outputs)")

    refs, queries, negs, splits = collect(rows, session, out_name)
    labeled = [q for q in queries if q[0] != NEG_CLASS]
    print(
        f"refs={len(refs)} queries={len(queries)}（有类标 {len(labeled)}） negs={len(negs)}"
        f"  manifest: ref={len(splits['ref'])} query={len(splits['query'])} neg={len(splits['neg'])}"
    )
    if not refs:
        print("[FAIL] 参考库为空：manifest 里 split=ref 且带 label 的条目为 0", file=sys.stderr)
        return 2
    if not labeled:
        print("[WARN] 查询集没有类别标签，只能算参考图自身一致性与负样本分布", file=sys.stderr)

    results = {}
    results["ref_self"] = evaluate(refs, refs, exclude_self=True)
    if negs:
        results["ref_to_neg"] = evaluate(refs, negs, exclude_self=False)
    if labeled:
        results["ref_to_query"] = evaluate(refs, labeled, exclude_self=False)
        results["query_self"] = evaluate(labeled, labeled, exclude_self=True)
        results["ref_plus_query_to_query"] = evaluate(refs + labeled, labeled, exclude_self=True)

    for name in ("ref_self", "ref_to_query", "query_self", "ref_plus_query_to_query", "ref_to_neg"):
        print_result(name, results.get(name))

    best = results.get("ref_plus_query_to_query") or results.get("ref_self")
    tau = suggest_tau(best["pos"], best["neg"]) if best else None
    margin = (
        round(float(np.percentile(best["margins"], 5)), 3) if best and best["margins"] else None
    )
    print("\n建议阈值")
    print(f"  tau    : {tau}")
    print(f"  margin : {margin}")
    print("  尺寸门限: 需在真实采集分辨率下确定")

    payload = {
        "counts": {
            "ref": len(refs),
            "query": len(queries),
            "query_labeled": len(labeled),
            "neg": len(negs),
        },
        "results": {
            name: {
                k: v for k, v in res.items()
                if k in ("n_ref", "n_query", "top1", "classes", "per_class", "confusion")
            }
            | {"pos": stats(res["pos"]), "neg": stats(res["neg"]), "margins": stats(res["margins"])}
            for name, res in results.items()
            if res
        },
        "suggest": {"tau": tau, "margin": margin},
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
