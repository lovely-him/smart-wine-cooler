"""采集方向标定：同一张实拍图试 4 个旋转方向，选检出最多、分数最高的那个。

为什么必须有这一步：`infer.rotate` 写错方向时检出数会**静默变成 0**，
而稳定窗照样判「可信」（它只看画面质量，不看检出数）。于是现象是"柜里有东西，
系统说没有"，且没有任何报错。换摄像头、换了安装角度、改了瓶子摆放朝向之后都要重标一次。

用法（在仓库根，宿主或板端都能跑）:
    python tools/lab/calibrate_rotate.py <图片或目录> [--config config/app.json]
"""

import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("[FAIL] 缺少 pillow，请用带依赖的 Python 运行", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import config as cfg_mod
from services.infer import load_detector

# ── 路径与常量 ──
EXTS = (".jpg", ".jpeg", ".png", ".bmp")
ANGLES = (0, 90, 180, 270)


# ── 函数 ──
def collect_images(target):
    if os.path.isdir(target):
        found = [
            os.path.join(target, name)
            for name in sorted(os.listdir(target))
            if name.lower().endswith(EXTS)
        ]
    else:
        found = [target]
    if not found:
        raise FileNotFoundError(f"没有图片: {target}")
    return found


def score_angles(cfg, images):
    """每个角度返回 (检出总数, 平均分数)，按图片累计。"""
    rows = []
    for angle in ANGLES:
        infer_cfg = dict(cfg["infer"], rotate=angle)
        detector = load_detector(infer_cfg)
        counts, scores = [], []
        try:
            for path in images:
                with Image.open(path) as fp:
                    frames = detector.detect(fp.convert("RGB"))
                counts.append(len(frames))
                scores.extend(item["score"] for item in frames)
        finally:
            detector.close()
        average = sum(scores) / len(scores) if scores else 0.0
        rows.append({"rotate": angle, "counts": counts, "total": sum(counts), "score": average})
    return rows


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="一张图或一个图片目录")
    parser.add_argument("--config", default=cfg_mod.DEFAULT_PATH)
    args = parser.parse_args()

    cfg = cfg_mod.load(args.config)
    images = collect_images(args.target)
    print(f"后端: {cfg['infer']['backend']}  图片: {len(images)} 张")
    rows = score_angles(cfg, images)

    print(f"\n{'rotate':>8} {'总检出':>7} {'平均分数':>9}  逐图检出")
    for row in rows:
        print(
            f"{row['rotate']:>8} {row['total']:>7} {row['score']:>9.3f}  {row['counts']}"
        )

    best = max(rows, key=lambda row: (row["total"], row["score"]))
    runner_up = sorted(rows, key=lambda row: (row["total"], row["score"]), reverse=True)[1]
    print(f"\n建议 rotate = {best['rotate']}（检出 {best['total']} 个，平均分 {best['score']:.3f}）")
    if runner_up["total"] == best["total"]:
        print(
            f"注意：rotate={runner_up['rotate']} 的检出数相同，分数只差 "
            f"{abs(runner_up['score'] - best['score']):.3f}——换个更典型的画面再标一次更稳。"
        )
    if best["total"] == 0:
        print("四个方向都检不出：先确认画面里真有目标、且光照与清晰度达标，再考虑重训模型。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
