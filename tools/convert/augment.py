"""为 INT8 量化生成校准图：对真实场景帧做轻度光度与缩放扰动。

只做亮度/对比度/gamma/缩放，不做旋转与翻转——校准只需要覆盖激活值范围，
不需要模拟姿态；几何变换会引入黑边，反而污染统计。

用法:
    python augment.py --images <源目录> --pick 1,3,5,7,9 --out <输出目录> [--per-image 10]
"""

import argparse
import os
import sys

try:
    from PIL import Image, ImageEnhance
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}，请用 tools/.venv 运行", file=sys.stderr)
    sys.exit(1)

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
VARIANTS = [
    (1.00, 1.00, 1.00, 1.00),
    (0.85, 1.00, 1.00, 1.00),
    (1.15, 1.00, 1.00, 1.00),
    (1.00, 0.90, 1.00, 1.00),
    (1.00, 1.10, 1.00, 1.00),
    (1.00, 1.00, 0.85, 1.00),
    (1.00, 1.00, 1.15, 1.00),
    (1.10, 1.10, 1.00, 1.00),
    (0.90, 0.90, 1.00, 1.00),
    (1.00, 1.00, 1.00, 1.05),
    (1.00, 1.00, 1.00, 0.95),
]


# ── 函数 ──
def apply_gamma(img, gamma):
    lut = [min(255, int(((i / 255.0) ** gamma) * 255)) for i in range(256)]
    return img.point(lut * 3)


def zoom(img, factor):
    width, height = img.size
    new_w = int(round(width * factor))
    new_h = int(round(height * factor))
    resized = img.resize((new_w, new_h), Image.BICUBIC)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def make_variants(img, limit):
    made = []
    for bright, contrast, gamma, scale in VARIANTS[:limit]:
        out = img
        if bright != 1.0:
            out = ImageEnhance.Brightness(out).enhance(bright)
        if contrast != 1.0:
            out = ImageEnhance.Contrast(out).enhance(contrast)
        if gamma != 1.0:
            out = apply_gamma(out, gamma)
        if scale != 1.0:
            out = zoom(out, scale)
        made.append(out)
    return made


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--pick", default="", help="只取第几张（1 起，逗号分隔）；空=全部")
    parser.add_argument("--out", required=True)
    parser.add_argument("--per-image", type=int, default=len(VARIANTS))
    args = parser.parse_args()

    if not os.path.isdir(args.images):
        print(f"[FAIL] 源目录不存在: {args.images}", file=sys.stderr)
        return 2
    limit = max(1, min(args.per_image, len(VARIANTS)))

    files = sorted(f for f in os.listdir(args.images) if f.lower().endswith(EXTS))
    if args.pick:
        wanted = [int(x) for x in args.pick.split(",") if x.strip()]
        files = [files[i - 1] for i in wanted if 0 < i <= len(files)]
    if not files:
        print("[FAIL] 没有选中任何图片", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    total = 0
    for name in files:
        with Image.open(os.path.join(args.images, name)) as fp:
            img = fp.convert("RGB")
        stem = os.path.splitext(name)[0][-12:].replace(" ", "_")
        for idx, variant in enumerate(make_variants(img, limit)):
            variant.save(os.path.join(args.out, f"{stem}_v{idx:02d}.jpg"), quality=95)
            total += 1
        print(f"{name[-26:]:26s} -> {limit} 变体")

    print(f"\n[ OK ] {len(files)} 张源图 -> {total} 张校准图 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
