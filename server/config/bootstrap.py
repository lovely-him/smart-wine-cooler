"""引导信息：数据库在哪、模型在哪、图片放哪。

这些**不进数据库**——如果数据库位置也存在数据库里，库一坏就永远起不来。
取得顺序：命令行 > 环境变量 > 这里给的约定位置。

真正可调的参数（阈值、触发、网络）在 `setting` 表里，见 `defaults.py`。
"""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env(path):
    """把仓库根的 .env 读进环境变量。

    已存在的变量不覆盖——命令行里临时指定的东西必须赢过文件。
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and key not in os.environ:
            os.environ[key] = value.strip().strip("'\"")


def _path(env, default):
    return Path(os.environ.get(env) or (ROOT / default))


def _int(env, default):
    raw = os.environ.get(env)
    return int(raw) if raw else default


_load_env(ROOT / ".env")

DATA_DIR = _path("DATA_DIR", "data")
DB_PATH = Path(os.environ.get("DB_PATH") or (DATA_DIR / "app.db"))
IMAGE_DIR = Path(os.environ.get("IMAGE_DIR") or (DATA_DIR / "images"))
REFERENCE_DIR = Path(os.environ.get("REFERENCE_DIR") or (DATA_DIR / "refs"))

DETECT_MODEL_RKNN = _path("DETECT_MODEL", "vendor/models/yolov8x_rk3588_fp16.rknn")
DETECT_MODEL_ONNX = _path("DETECT_MODEL_ONNX", "tools/embed_lab/models/yolov8x/yolov8x.onnx")
EMBED_MODEL_RKNN = _path("EMBED_MODEL", "vendor/models/mobilenetv2_pool_rk3588_fp16.rknn")
EMBED_MODEL_ONNX = _path("EMBED_MODEL_ONNX", "tools/embed_lab/models/mobilenetv2_pool.onnx")

# 采集设备与画面尺寸：一台设备上固定，不属于用户可调项
# 宿主机没有摄像头，用 image_dir 指向一组图片；板子用 v4l2
CAMERA_BACKEND = os.environ.get("CAMERA_BACKEND", "v4l2")
CAMERA_DEVICE = os.environ.get(
    "CAMERA_DEVICE",
    "/dev/v4l/by-id/usb-ALC-251223-CX_USB_Camera_01.00.00-video-index0",
)
CAMERA_IMAGE_DIR = _path("CAMERA_IMAGE_DIR", "data/samples/layer1")
CAMERA_WIDTH = _int("CAMERA_WIDTH", 1280)
CAMERA_HEIGHT = _int("CAMERA_HEIGHT", 720)

# 模型固定参数：随模型走，换模型时跟着改，不是用户可调项
DETECT_INPUT_SIZE = _int("DETECT_INPUT_SIZE", 640)
DETECT_CLASS_ID = _int("DETECT_CLASS_ID", 39)
DETECT_IOU = 0.45
EMBED_INPUT_SIZE = _int("EMBED_INPUT_SIZE", 224)


def _backend(kind):
    """推理后端：显式指定优先，否则看本机有没有 NPU 运行时。"""
    explicit = os.environ.get(f"{kind}_BACKEND")
    if explicit:
        return explicit
    return "rknn" if importlib.util.find_spec("rknnlite") else "onnx"


def infer_setup():
    """返回 (检测后端, 检测模型, 特征后端, 特征模型)。"""
    detect = _backend("DETECT")
    embed = _backend("EMBED")
    return (
        detect,
        DETECT_MODEL_RKNN if detect == "rknn" else DETECT_MODEL_ONNX,
        embed,
        EMBED_MODEL_RKNN if embed == "rknn" else EMBED_MODEL_ONNX,
    )
