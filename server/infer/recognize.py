"""AI 补全类别表单：图片 + 表单已填内容 → 空字段的建议。

类别详情是人填的，它只负责把空着的格子查出来。输入里**表单已填的内容才是检索锚点**——
图上看不清是常态（反光、遮挡、瓶子上的字被框切掉），人填的品牌、年份、条码才让它查得准。

返回的一切都只是候选：不覆盖已填字段、不落库，失败只回错误原因，不影响任何主链路。
"""

import base64
import io
import json
import logging
import re
import time

from server.models.fields import ASKABLE

logger = logging.getLogger(__name__)

TIMEOUT = 300
JPEG_QUALITY = 88
JSON_BLOCK = re.compile(r"\{.*\}", re.S)
WEB_SEARCH = {"type": "deepseek_web_search", "name": "deepseek_web_search"}


def build_prompt(known, targets, labels=None):
    """提示词由表单生成：已填的当锚点，只问空着的那几个字段。"""
    names = labels or {item.key: item.label for item in ASKABLE}
    lines = [
        "这是一个商品（可能是酒或饮料）的图片，可能有多张：第一张是柜内实际拍到的这一件，"
        "其余是同一类别的历史参考图。请把它当成一次商品资料调研。",
        "",
    ]
    anchor = {key: value for key, value in (known or {}).items() if str(value).strip()}
    if anchor:
        lines.append("已知信息（人已经确认过，**以此为准，不要改**）：")
        lines.extend(f"- {names.get(key, key)}：{value}" for key, value in anchor.items())
    else:
        lines.append("没有任何已知信息，请先根据图片判断这是什么。")
    lines += ["", "需要你补全的字段（**只回答这些**）："]
    lines.extend(f"- {key}（{names.get(key, key)}）" for key in targets)
    lines += [
        "",
        "怎么作答：",
        "1. 先看图上印着的字，能认出的原文写进 labelText，认不清的写「看不清」；",
        "2. 再联网核实：有已知信息就按已知信息查，没有就按图上读到的字查，查到的资料尽量补全；",
        "3. 每条建议标明来源：label = 图上直接读到，web = 联网查到；",
        "4. 与已知信息不一致的，不要改也不要忽略，放进 conflicts；",
        "5. 查不到的字段直接不出现，**不要编造**。",
        "",
        "只输出一个 JSON 对象，前后不要任何解释文字：",
        '{"labelText": "...", "name": "仅当名称为空时给：品牌+品名，不超过 16 字",',
        ' "suggestions": {"字段键": {"value": "...", "basis": "label"}},',
        ' "conflicts": [{"key": "字段键", "known": "已知值", "found": "查到值", "basis": "web"}],',
        ' "uncertain": ["读不清或存疑的说明"]}',
    ]
    return "\n".join(lines)


def empty_result():
    return {
        "labelText": "",
        "name": "",
        "suggestions": {},
        "conflicts": [],
        "uncertain": [],
        "error": "",
    }


def encode_jpeg(image, max_side):
    from PIL import Image

    work = image.convert("RGB")
    if max(work.size) > max_side:
        work.thumbnail((max_side, max_side), Image.BILINEAR)
    buffer = io.BytesIO()
    work.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def parse_suggest(content, targets, known):
    """从模型回复里取出建议；只留目标字段、丢掉已填的（防覆盖）。"""
    text = (content or "").strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return empty_result()

    known_map = {key: str(value) for key, value in (known or {}).items() if str(value).strip()}
    wanted = set(targets)
    suggestions = {}
    for key, item in (data.get("suggestions") or {}).items():
        if key not in wanted or key in known_map or not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        basis = "label" if str(item.get("basis") or "").strip() == "label" else "web"
        suggestions[key] = {"value": value, "basis": basis}

    conflicts = []
    for item in data.get("conflicts") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        found = str(item.get("found") or "").strip()
        if key not in known_map or not found:
            continue
        basis = "label" if str(item.get("basis") or "").strip() == "label" else "web"
        conflicts.append({"key": key, "known": known_map[key], "found": found, "basis": basis})

    uncertain = [
        str(item).strip() for item in (data.get("uncertain") or []) if str(item).strip()
    ]

    return {
        "labelText": str(data.get("labelText") or "").strip()[:400],
        "name": str(data.get("name") or "").strip()[:24] if not known_map.get("名称") else "",
        "suggestions": suggestions,
        "conflicts": conflicts,
        "uncertain": uncertain[:8],
        "error": "",
    }


class Recognizer:
    """OpenAI 兼容的 chat/completions 客户端，型号必须是支持看图的。"""

    def __init__(self, cfg):
        self.endpoint = (cfg.get("endpoint") or "").rstrip("/")
        self.key = cfg.get("key") or ""
        self.model = cfg.get("model") or ""
        self.max_side = int(cfg.get("max_side") or 1280)

    def available(self):
        return bool(self.endpoint and self.key and self.model)

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}"}

    def suggest(self, images, known, targets, deep=False, labels=None):
        if not self.available():
            return {**empty_result(), "error": "未配置大模型"}

        import requests

        session = requests.Session()
        try:
            content = [{"type": "text", "text": build_prompt(known, targets, labels)}]
            for image in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + encode_jpeg(image, self.max_side),
                            "detail": "high",
                        },
                    }
                )
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 2048,
                "tools": [WEB_SEARCH],
                "thinking": {"type": "enabled" if deep else "disabled"},
            }
            if deep:
                payload["reasoning_effort"] = "low"

            started = time.time()
            resp = session.post(
                f"{self.endpoint}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("AI 补全失败：HTTP %s %s", resp.status_code, resp.text[:200])
                return {**empty_result(), "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            parsed = parse_suggest(data["choices"][0]["message"].get("content"), targets, known)
            usage = data.get("usage") or {}
            tools = usage.get("server_tool_use") or {}
            logger.info(
                "AI 补全：%.1fs，token %s，搜索 %s 次，图 %d 张，建议 %d 项，冲突 %d 项，读图「%s」",
                time.time() - started,
                usage.get("total_tokens"),
                (tools.get("tool_search") or 0) + (tools.get("tool_image_search") or 0),
                len(images),
                len(parsed["suggestions"]),
                len(parsed["conflicts"]),
                parsed["labelText"] or "无",
            )
            return parsed
        except Exception as exc:
            logger.warning("AI 补全异常：%s: %s", exc.__class__.__name__, exc)
            return {**empty_result(), "error": f"{exc.__class__.__name__}: {exc}"}
        finally:
            session.close()


def load_recognizer(cfg):
    """cfg 来自配置库的 ai.* 一组。关闭或没配密钥时返回 None。"""
    if not cfg.get("enabled"):
        return None
    recognizer = Recognizer(cfg)
    return recognizer if recognizer.available() else None


CONFIG_KEYS = ("ai.enabled", "ai.endpoint", "ai.key", "ai.model", "ai.max_side")


def from_config(config):
    """从配置库取一整组 ai.* 建识别器；没配好就返回 None。"""
    return load_recognizer({key.split(".", 1)[1]: config.get(key) for key in CONFIG_KEYS})


def call_suggest(recognizer, images, known, targets, deep=False, attempts=2, labels=None):
    last = None
    for _ in range(attempts):
        last = recognizer.suggest(images, known, targets, deep, labels)
        if not last["error"]:
            return last
        time.sleep(1.0)
    return last


USAGE_KEY = "ai_usage"


def read_usage(db, day):
    """今天已经问过几次。过了一天就归零，不需要谁去清。"""
    row = db.conn.execute("SELECT value FROM meta WHERE key=?", (USAGE_KEY,)).fetchone()
    if row is None:
        return 0
    try:
        data = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return 0
    return int(data.get("count", 0)) if data.get("day") == day else 0


def bump_usage(db, day):
    count = read_usage(db, day) + 1

    def _write(conn):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (USAGE_KEY, json.dumps({"day": day, "count": count})),
        )
        return count

    return db.write(_write)
