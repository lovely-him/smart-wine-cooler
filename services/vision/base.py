"""视觉大模型能力层的统一契约。

`describe()` **永远不抛异常**：超时、连接拒绝、鉴权失败、返回格式异常一律返回 None，
调用方看到 None 就退回"用户手填"，不做自动重试。
"""

import base64
import io

# ── 路径与常量 ──
MAX_SIDE = 512


# ── 函数 ──
def encode_jpeg(image, max_side=MAX_SIDE):
    """缩到长边 max_side 再编 JPEG base64：原图直传会把 token 抬到几千，没必要。"""
    img = image.convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ── 类 ──
class Provider:
    name = "base"
    network = False

    def available(self):
        return False

    def describe(self, image):
        """返回 {"name": 候选名, "confidence": 0~1}；判不出返回 None。"""
        return None

    def close(self):
        return None
