"""把 ONNX 模型转成 Rockchip RKNN 模型（仅 x86 可用）。

默认走 FP16（不做量化）：数值最接近源 ONNX，用于先把"转换 → 板端推理 → 门禁"这条链路跑通。
量化（INT8）需要一批校准图，等链路稳定后再单独验证，避免首次门禁混入量化误差。

用法:
    python convert_onnx.py --onnx <in.onnx> --out <out.rknn> [--target rk3588] [--quant-img <目录>]
"""

import argparse
import os
import sys

try:
    from rknn.api import RKNN
except ImportError as exc:
    print(f"[FAIL] 缺少 {exc.name}，请用 tools/.venv 运行，或先执行 tools/setup 安装", file=sys.stderr)
    sys.exit(1)

# ── 路径与常量 ──
DEFAULT_TARGET = "rk3588"
DEFAULT_INPUT = 640
DEFAULT_INPUT_NAME = "images"
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKDIR = os.path.join(ROOT, "cache", "build", "convert")
CHECK_PREFIX = "check"


# ── 函数 ──
def detect_input_name(onnx_path):
    """从 onnx 图里取第一个真正的输入名（排除权重初始化节点）。"""
    import onnx

    model = onnx.load(onnx_path, load_external_data=False)
    inits = {item.name for item in model.graph.initializer}
    names = [i.name for i in model.graph.input if i.name not in inits]
    if not names:
        raise RuntimeError(f"模型里找不到输入节点: {onnx_path}")
    return names[0]


def parse_triple(text, default):
    """"r,g,b" → [float, float, float]。"""
    if not text:
        return list(default)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"需要三个数（r,g,b）: {text}")
    return [float(p) for p in parts]


def enter_workdir():
    """切到工作目录：rknn 的图优化器会把每个 pass 的中间 onnx 直接写到 CWD，无处可配。"""
    os.makedirs(WORKDIR, exist_ok=True)
    for name in os.listdir(WORKDIR):
        if name.startswith(CHECK_PREFIX) and name.endswith(".onnx"):
            os.remove(os.path.join(WORKDIR, name))
    os.chdir(WORKDIR)
    print(f"[INFO] 工作目录: {WORKDIR}")


def build_dataset(quant_dir):
    files = sorted(f for f in os.listdir(quant_dir) if f.lower().endswith(EXTS))
    if not files:
        raise RuntimeError(f"校准目录里没有图片: {quant_dir}")
    list_path = os.path.join(quant_dir, "dataset.txt")
    with open(list_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(os.path.abspath(os.path.join(quant_dir, f)) for f in files))
    print(f"[INFO] 校准图 {len(files)} 张 -> {list_path}")
    return list_path


def build_rknn(onnx_path, out_path, target, input_size, input_name, quant_dir, quant_algorithm, mean, std):
    quantized = quant_dir is not None
    rknn = RKNN(verbose=True)

    rknn.config(
        mean_values=[mean],
        std_values=[std],
        target_platform=target,
        quantized_dtype="w8a8",
        quantized_algorithm=quant_algorithm,
        optimization_level=3,
    )

    ret = rknn.load_onnx(
        model=onnx_path,
        inputs=[input_name],
        input_size_list=[[1, 3, input_size, input_size]],
    )
    if ret != 0:
        raise RuntimeError(f"load_onnx 失败: ret={ret}")

    dataset = build_dataset(quant_dir) if quantized else None
    ret = rknn.build(do_quantization=quantized, dataset=dataset)
    if ret != 0:
        raise RuntimeError(f"build 失败: ret={ret}")

    ret = rknn.export_rknn(out_path)
    if ret != 0:
        raise RuntimeError(f"export_rknn 失败: ret={ret}")

    rknn.release()
    return quantized


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--input-size", type=int, default=DEFAULT_INPUT)
    parser.add_argument("--input-name", default="", help="留空则从 onnx 图里自动识别")
    parser.add_argument("--quant-img", default="", help="校准图目录；给出则做 INT8 量化")
    parser.add_argument("--quant-algorithm", default="normal", choices=("normal", "mmse", "kl_divergence"))
    parser.add_argument("--mean", default="", help="r,g,b（0~255 尺度）；留空为 0,0,0")
    parser.add_argument("--std", default="", help="r,g,b（0~255 尺度）；留空为 255,255,255")
    args = parser.parse_args()

    if not os.path.isfile(args.onnx):
        print(f"[FAIL] ONNX 不存在: {args.onnx}", file=sys.stderr)
        return 2
    if args.quant_img and not os.path.isdir(args.quant_img):
        print(f"[FAIL] 校准图目录不存在: {args.quant_img}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    mode = "INT8 量化" if args.quant_img else "FP16（不量化）"
    try:
        mean = parse_triple(args.mean, (0.0, 0.0, 0.0))
        std = parse_triple(args.std, (255.0, 255.0, 255.0))
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"[INFO] {args.onnx}")
    print(f"[INFO]   -> {args.out}   目标={args.target} 输入={args.input_size} 模式={mode}")
    print(f"[INFO]   mean={mean} std={std}")

    onnx_path = os.path.abspath(args.onnx)
    out_path = os.path.abspath(args.out)
    quant_path = os.path.abspath(args.quant_img) if args.quant_img else None
    try:
        input_name = args.input_name or detect_input_name(onnx_path)
    except (RuntimeError, ImportError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"[INFO]   输入名={input_name}")
    enter_workdir()

    quantized = build_rknn(
        onnx_path,
        out_path,
        args.target,
        args.input_size,
        input_name,
        quant_path,
        args.quant_algorithm,
        mean,
        std,
    )

    if not os.path.isfile(out_path):
        print("[FAIL] 转换后没有产物", file=sys.stderr)
        return 1
    size_mb = os.path.getsize(out_path) / 1048576
    print(f"[ OK ] 已生成 {args.out}  ({size_mb:.1f} MB, {'INT8' if quantized else 'FP16'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
