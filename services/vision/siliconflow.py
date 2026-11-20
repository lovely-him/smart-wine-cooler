"""硅基流动（OpenAI 兼容）实现。

约束：型号不写死——`/models` 里现役的视觉模型才是可用型号，写死的名字下个月可能就没了。
"""

import json

import requests

from .base import Provider, encode_jpeg

# ── 路径与常量 ──
BASE_URL = "https://api.siliconflow.cn/v1"
KEY_ENV = "SILICONFLOW_API_KEY"
PROMPT = (
    "图片是从货架照片里裁出的一件商品。请给出这件商品最常见的商品名：中文，"
    "品牌+品名即可，不超过12个字，不要标点，不要解释。"
    "看不清或不是商品时 name 返回空串、confidence 返回 0。"
)
SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["name", "confidence"],
    "additionalProperties": False,
}
VISION_HINTS = ("VL-", "4v", "4.1v")
EXCLUDE_HINTS = ("Embedding", "Reranker", "OCR", "Thinking")


# ── 类 ──
class SiliconFlow(Provider):
    name = "siliconflow"
    network = True

    def __init__(self, base_url=BASE_URL, api_key="", model="", timeout=30, max_side=512):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.max_side = int(max_side)
        self.session = requests.Session()
        self._resolved = ""

    def available(self):
        return bool(self.api_key)

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def resolve_model(self):
        """配置为空或平台没有该型号时，从 /models 挑一个在役视觉模型。"""
        if self._resolved:
            return self._resolved
        try:
            resp = self.session.get(f"{self.base_url}/models", headers=self._headers(), timeout=self.timeout)
            ids = [item.get("id", "") for item in resp.json().get("data", [])]
        except Exception:
            ids = []
        if ids and self.model in ids:
            self._resolved = self.model
            return self._resolved
        for model_id in ids:
            if any(hint in model_id for hint in VISION_HINTS) and not any(
                bad in model_id for bad in EXCLUDE_HINTS
            ):
                self._resolved = model_id
                return self._resolved
        self._resolved = self.model
        return self._resolved

    def describe(self, image):
        try:
            model = self.resolve_model()
            if not model:
                return None
            body = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encode_jpeg(image, self.max_side)}"},
                            },
                        ],
                    }
                ],
                "response_format": {"type": "json_schema", "json_schema": {"name": "label", "schema": SCHEMA}},
                "temperature": 0.1,
                "max_tokens": 64,
            }
            resp = self.session.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code != 200:
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            name = str(data.get("name") or "").strip()
            if not name:
                return None
            return {"name": name[:24], "confidence": float(data.get("confidence") or 0.0)}
        except Exception:
            return None

    def close(self):
        self.session.close()
