"""检测后处理：letterbox 预处理、DFL 解码、NMS、旋转坐标反变换。

宿主（onnxruntime）与板卡（rknnlite）必须走**同一份**后处理，否则门禁比出来的差异
会被误当成量化误差。所以这些函数放在这里，两个推理后端只负责"把张量喂进去、拿出来"。

导出的模型是未解码的原始头（每格 144 通道 = 4×16 DFL + 80 类），解码逻辑：
每格取 4 个距离分布（DFL 期望）× stride 得到 ltrb，再换算成原图坐标。
"""

import numpy as np
from PIL import Image

# ── 路径与常量 ──
DEFAULT_SIZE = 640
DEFAULT_PAD = 114
DEFAULT_REG_MAX = 16
DEFAULT_STRIDES = (8, 16, 32)
DEFAULT_IOU = 0.45


# ── 函数 ──
def letterbox(img, size=DEFAULT_SIZE, pad_value=DEFAULT_PAD):
    """等比缩放后居中填充成正方形画布。返回画布与还原所需的几何参数。"""
    width, height = img.size
    scale = min(size / width, size / height)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resized = np.asarray(img.resize((new_w, new_h), Image.BILINEAR), dtype=np.float32)
    canvas = np.full((size, size, 3), pad_value, dtype=np.float32)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y, width, height


def to_nchw(canvas):
    """float32 画布 → 归一化 NCHW 张量（onnx 后端使用）。"""
    return (canvas / 255.0).transpose(2, 0, 1)[None].astype(np.float32)


def nms(boxes, scores, iou_thres=DEFAULT_IOU):
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        idx = order[0]
        keep.append(int(idx))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[idx, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[idx, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[idx, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[idx, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[idx, 2] - boxes[idx, 0]) * (boxes[idx, 3] - boxes[idx, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / (area_i + area_r - inter + 1e-6)
        order = rest[iou <= iou_thres]
    return keep


def decode(
    outputs,
    scale,
    pad_x,
    pad_y,
    width,
    height,
    conf,
    cls_id,
    reg_max=DEFAULT_REG_MAX,
    strides=DEFAULT_STRIDES,
    iou_thres=DEFAULT_IOU,
):
    """原始头 → 原图坐标下的框与分数（已做 NMS）。"""
    boxes, scores = [], []
    for level, out in enumerate(outputs):
        stride = strides[level]
        feat = out[0].reshape(out.shape[1], -1)
        cls = 1.0 / (1.0 + np.exp(-feat[reg_max * 4 + cls_id]))
        pick = np.nonzero(cls >= conf)[0]
        if pick.size == 0:
            continue
        reg = feat[: reg_max * 4][:, pick].reshape(4, reg_max, -1)
        reg = reg - reg.max(axis=1, keepdims=True)
        prob = np.exp(reg)
        prob /= prob.sum(axis=1, keepdims=True)
        bins = np.arange(reg_max, dtype=np.float32).reshape(1, reg_max, 1)
        dist = (prob * bins).sum(axis=1) * stride
        gw = out.shape[3]
        xs = (pick % gw + 0.5) * stride
        ys = (pick // gw + 0.5) * stride
        boxes.append(np.stack([xs - dist[0], ys - dist[1], xs + dist[2], ys + dist[3]], axis=1))
        scores.append(cls[pick])
    if not boxes:
        return []

    box = np.concatenate(boxes).astype(np.float32)
    score = np.concatenate(scores).astype(np.float32)
    hits = []
    for idx in nms(box, score, iou_thres):
        x1 = float(np.clip((box[idx, 0] - pad_x) / scale, 0, width))
        y1 = float(np.clip((box[idx, 1] - pad_y) / scale, 0, height))
        x2 = float(np.clip((box[idx, 2] - pad_x) / scale, 0, width))
        y2 = float(np.clip((box[idx, 3] - pad_y) / scale, 0, height))
        hits.append({"score": float(score[idx]), "box": [x1, y1, x2, y2]})
    return hits


def to_original(box, deg, width, height):
    """把旋转后画面上的框映射回原图坐标。width/height 是**原图**尺寸。"""
    x1, y1, x2, y2 = box
    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    if deg == 0:
        mapped = pts
    elif deg == 90:
        mapped = [((width - 1) - y, x) for x, y in pts]
    elif deg == 180:
        mapped = [((width - 1) - x, (height - 1) - y) for x, y in pts]
    else:
        mapped = [(y, (height - 1) - x) for x, y in pts]
    xs = [p[0] for p in mapped]
    ys = [p[1] for p in mapped]
    return [
        max(0.0, min(xs)),
        max(0.0, min(ys)),
        min(float(width), max(xs)),
        min(float(height), max(ys)),
    ]
