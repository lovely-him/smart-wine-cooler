"""推理后端与公共后处理。"""


# ── 函数 ──
def load_detector(cfg):
    """配置里写 backend，业务层拿到的都是一个 Detector。"""
    backend = cfg["backend"]
    if backend == "onnx":
        from services.infer.onnx_backend import OnnxDetector

        return OnnxDetector(cfg)
    if backend == "rknn":
        from services.infer.rknn_backend import RknnDetector

        return RknnDetector(cfg)
    raise ValueError(f"未知的推理后端: {backend}（可选 onnx / rknn）")


def load_embedder(cfg):
    """特征后端：与检测并列，业务层只看到 embed(img) -> 归一化向量。"""
    backend = cfg["backend"]
    if backend == "onnx":
        from services.infer.embed_onnx import OnnxEmbedder

        return OnnxEmbedder(cfg)
    if backend == "rknn":
        from services.infer.embed_rknn import RknnEmbedder

        return RknnEmbedder(cfg)
    raise ValueError(f"未知的特征后端: {backend}（可选 onnx / rknn）")


