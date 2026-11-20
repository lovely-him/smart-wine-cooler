"""用宿主侧 YOLOv8n（COCO）检测瓶子并裁剪：支持绕光轴旋转、多方向合并、位置标注与落库。

导出的 ONNX 是未解码的原始头（每格 144 通道 = 4×16 DFL + 80 类），这里自己做 DFL 解码与 NMS。
通用检测器对**横躺**瓶子几乎不响应（会被判成 fork/banana/cell phone），绕光轴旋转到瓶轴竖直后
才恢复，所以旋转角默认 90°，且必须与实拍机位绑定，不能靠自适应猜。
"""

import argparse
import json
import os
import sys

try:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageDraw
except ImportError as exc:
    print(f"[FAIL] 缺少依赖 {exc.name}，执行: ./tools/embed_lab/run.sh setup", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.infer import postproc

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
INPUT = 640
BOTTLE_CLS = 39
MERGE_IOU = 0.4
DEFAULT_ROTATE = "90"


# ── 函数 ──
def load_session(model_path):
    if not os.path.isfile(model_path):
        raise RuntimeError(f"缺少检测模型 {model_path}，先跑 setup")
    return ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])


def merge(boxes):
    boxes = sorted(boxes, key=lambda b: -b["score"])
    keep = []
    for cur in boxes:
        b = cur["box"]
        dup = False
        for other in keep:
            k = other["box"]
            ix = min(b[2], k[2]) - max(b[0], k[0])
            iy = min(b[3], k[3]) - max(b[1], k[1])
            if ix <= 0 or iy <= 0:
                continue
            inter = ix * iy
            union = (b[2] - b[0]) * (b[3] - b[1]) + (k[2] - k[0]) * (k[3] - k[1]) - inter
            if union > 0 and inter / union > MERGE_IOU:
                dup = True
                break
        if not dup:
            keep.append(cur)
    return keep


def detect(session, img, rotations, conf):
    name = session.get_inputs()[0].name
    hits = []
    for deg in rotations:
        work = img.rotate(deg, expand=True) if deg else img
        canvas, scale, pad_x, pad_y, width, height = postproc.letterbox(work, INPUT)
        outputs = session.run(None, {name: postproc.to_nchw(canvas)})
        for hit in postproc.decode(outputs, scale, pad_x, pad_y, width, height, conf, BOTTLE_CLS):
            hits.append({"score": hit["score"], "box": postproc.to_original(hit["box"], deg, *img.size)})
    return merge(hits)


def position_names(count):
    if count <= 1:
        return ("单",)
    if count == 2:
        return ("上", "下")
    if count == 3:
        return ("上", "中", "下")
    return tuple(["上"] + ["中"] * (count - 2) + ["下"])


def safe_name(text):
    keep = [c if c.isalnum() or c in "-_." else "_" for c in text]
    return "".join(keep).strip("_") or "batch"


def save_crop(img, box, pad, path):
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    rect = (
        int(max(0, x1 - bw * pad)),
        int(max(0, y1 - bh * pad)),
        int(min(img.size[0], x2 + bw * pad)),
        int(min(img.size[1], y2 + bh * pad)),
    )
    img.crop(rect).save(path, quality=92)


def annotate(img, items, path):
    canvas = img.copy()
    draw = ImageDraw.Draw(canvas)
    for item in items:
        x1, y1, x2, y2 = item["box"]
        draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=4)
        draw.text(
            (x1 + 6, max(0, y1 - 13)),
            f"#{item['index']}{item['pos']} {item['score']:.2f}",
            fill=(0, 255, 0),
        )
    canvas.save(path, quality=88)


def append_manifest(path, rows):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fresh = {row["path"] for row in rows}
    kept = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("path") not in fresh:
                    kept.append(row)
    kept.extend(rows)
    with open(path, "w", encoding="utf-8") as fp:
        for row in kept:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", required=True, help="裁剪图输出目录")
    parser.add_argument("--annot", default="", help="标注图输出目录（留空则不生成）")
    parser.add_argument("--rotate", default=DEFAULT_ROTATE, help="绕光轴旋转角，可写 0,90,180,270")
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--pad", type=float, default=0.06)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--batch", default="")
    args = parser.parse_args()

    if not os.path.isdir(args.src):
        print(f"[FAIL] 输入目录不存在: {args.src}", file=sys.stderr)
        return 2
    rotations = [int(v) for v in args.rotate.split(",") if v.strip() != ""]
    batch = safe_name(args.batch or os.path.basename(os.path.abspath(args.src)))

    os.makedirs(args.out, exist_ok=True)
    if args.annot:
        os.makedirs(args.annot, exist_ok=True)

    session = load_session(args.model)
    files = sorted(f for f in os.listdir(args.src) if f.lower().endswith(EXTS))
    if not files:
        print(f"[FAIL] {args.src} 下没有图片", file=sys.stderr)
        return 1

    report = {"batch": batch, "rotate": rotations, "min_score": args.min_score, "images": []}
    rows = []
    probe_conf = min(args.min_score, 0.05)

    for idx, name in enumerate(files, start=1):
        with Image.open(os.path.join(args.src, name)) as fp:
            img = fp.convert("RGB")
        width, height = img.size
        hits = [h for h in detect(session, img, rotations, probe_conf) if h["score"] >= args.min_score]
        hits.sort(key=lambda h: (h["box"][1] + h["box"][3]) / 2)
        names = position_names(len(hits))
        print(f"\n#{idx:02d} {name}  ({width}x{height})  -> {len(hits)} 个")

        items = []
        for k, hit in enumerate(hits, start=1):
            x1, y1, x2, y2 = hit["box"]
            pos = names[k - 1]
            crop_name = f"{batch}_i{idx:02d}_b{k}{pos}.jpg"
            save_crop(img, hit["box"], args.pad, os.path.join(args.out, crop_name))
            rows.append(
                {
                    "path": f"crop/{crop_name}",
                    "split": "crop",
                    "label": "",
                    "source": "detect",
                    "scene": batch,
                    "note": f"{name} score={hit['score']:.3f} 位置={pos} 第{k}/{len(hits)}",
                }
            )
            items.append(
                {"index": k, "pos": pos, "score": hit["score"], "box": hit["box"], "crop": crop_name}
            )
            print(
                f"    #{k}{pos} score={hit['score']:.3f} x=[{x1:.0f},{x2:.0f}] "
                f"y=[{y1:.0f},{y2:.0f}] 尺寸={x2 - x1:.0f}x{y2 - y1:.0f} "
                f"中心=({(x1 + x2) / 2:.0f},{(y1 + y2) / 2:.0f}) -> {crop_name}"
            )

        if args.annot:
            annotate(img, items, os.path.join(args.annot, f"{batch}_i{idx:02d}.jpg"))
        report["images"].append(
            {"file": name, "width": width, "height": height, "count": len(hits), "boxes": items}
        )

    append_manifest(args.manifest, rows)

    total = sum(item["count"] for item in report["images"])
    print(f"\n[ OK ] {len(files)} 张图，共检出 {total} 个瓶子，裁剪 {len(rows)} 张 -> {args.out}")
    if args.manifest:
        print(f"       manifest 追加 {len(rows)} 条 -> {args.manifest}")
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
        print(f"       report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
