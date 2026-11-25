"""配置读写：出厂默认值 + 数据库覆盖值。

敏感项（密钥、口令）读回时一律打码，变更记录里只记"改过"、不记新旧明文。
值按 JSON 存，所以数字/布尔/字符串不需要额外的类型转换表。
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime

from .defaults import (
    BY_KEY,
    DEFAULTS,
    GROUP_TITLES,
    GROUPS,
    ITEMS,
    NOW,
    SETTINGS,
)

MASK = "********"
HASH_PREFIX = "sha256$"


def _digest(passcode, salt):
    return hashlib.sha256(f"{salt}{passcode}".encode("utf-8")).hexdigest()


def encode_passcode(passcode):
    """口令只存不可逆摘要。空口令存空串（表示未设）。"""
    if not passcode:
        return ""
    salt = secrets.token_hex(16)
    return f"{HASH_PREFIX}{salt}${_digest(passcode, salt)}"


def passcode_matches(stored, given):
    """旧版明文口令直接比对一次，下次写入时自动转成摘要。"""
    stored = str(stored or "")
    if not stored:
        return False
    if not stored.startswith(HASH_PREFIX):
        return hmac.compare_digest(stored, str(given or ""))
    _, salt, digest = stored.split("$", 2)
    return hmac.compare_digest(_digest(str(given or ""), salt), digest)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ConfigStore:
    def __init__(self, db):
        self.db = db
        self._cache = None

    def load(self):
        values = dict(DEFAULTS)
        for row in self.db.conn.execute("SELECT key, value FROM setting"):
            if row["key"] not in BY_KEY:
                continue
            try:
                values[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        self._cache = values
        return values

    def all(self):
        return self._cache if self._cache is not None else self.load()

    def get(self, key):
        return self.all().get(key, DEFAULTS.get(key))

    def _shown(self, key, mask):
        value = self.get(key)
        if mask and BY_KEY[key].sensitive and value:
            return MASK
        return value

    @staticmethod
    def _effect_of(keys):
        order = {"now": 0, "service": 1, "device": 2}
        return max(
            (BY_KEY[key].effect for key in keys if key in BY_KEY),
            key=lambda item: order.get(item, 0),
            default=NOW,
        )

    def groups(self, mask=True):
        result = []
        for gid, title in GROUPS:
            items = []
            for item in ITEMS:
                if item.group != gid:
                    continue
                items.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "effect": self._effect_of(item.keys),
                        "danger": any(BY_KEY[key].danger for key in item.keys),
                        "sensitive": any(BY_KEY[key].sensitive for key in item.keys),
                        "controls": [
                            {
                                "key": key,
                                "kind": BY_KEY[key].kind,
                                "label": BY_KEY[key].label,
                                "value": self._shown(key, mask),
                                "options": list(BY_KEY[key].choices),
                            }
                            for key in item.keys
                        ],
                    }
                )
            result.append({"id": gid, "title": title, "items": items})
        return result

    def changes(self, limit=50):
        owner = {key: item for item in ITEMS for key in item.keys}
        result = []
        for row in self.history(limit):
            item = owner.get(row["key"])
            if item is None:
                continue
            result.append(
                {
                    "id": row["id"],
                    "itemId": item.id,
                    "title": item.title,
                    "at": row["at"],
                    "note": "已修改" if row["sensitive"] else self._before_after(row),
                }
            )
        return result

    @staticmethod
    def _before_after(row):
        def text(raw):
            if raw is None:
                return "（空）"
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return str(raw)
            return "（空）" if value == "" else str(value)

        return f"{text(row['old'])} → {text(row['new'])}"

    def state(self, mask=True):
        return {"groups": self.groups(mask), "changes": self.changes()}

    def danger_keys(self):
        return {item.key for item in SETTINGS if item.danger}

    def guard(self, values, given):
        """危险项要过口令。还没设过口令时，只允许把口令本身设上。"""
        dangerous = [key for key in values if key in BY_KEY and BY_KEY[key].danger]
        if not dangerous:
            return
        stored = str(self.get("sec.passcode") or "")
        if not stored:
            if set(dangerous) - {"sec.passcode"}:
                raise PermissionError("还没设设备口令，先设口令再改危险配置")
        elif not self.check_passcode(given):
            raise PermissionError("口令不对")
        self._guard_hint(values)

    def check_passcode(self, given):
        return passcode_matches(self.get("sec.passcode"), given)

    def _guard_hint(self, values):
        """有口令就必须有提示——忘了口令时只能靠它。"""
        if "sec.passcode" not in values:
            return
        passcode = values["sec.passcode"]
        if passcode == MASK:
            passcode = self.get("sec.passcode")
        if not str(passcode or "").strip():
            return
        hint = values.get("sec.passcode_hint", self.get("sec.passcode_hint"))
        if not str(hint or "").strip():
            raise ValueError("设口令要同时写一句口令提示，忘了口令时靠它")

    def set_many(self, group, values):
        """写入一组配置。返回 (改动过的键, 需要重启服务的键, 需要重启设备的键)。"""
        unknown = [k for k in values if k not in BY_KEY]
        if unknown:
            raise KeyError(f"未知配置项: {', '.join(unknown)}")
        wrong = [k for k in values if BY_KEY[k].group != group]
        if wrong:
            raise KeyError(f"这些项不属于 {group}: {', '.join(wrong)}")

        if "sec.passcode" in values and not str(values["sec.passcode"] or ""):
            values = {**values, "sec.passcode_hint": ""}

        current = self.all()
        changed, need_service, need_device = [], [], []
        entries = []
        for key, raw in values.items():
            item = BY_KEY[key]
            value = self._coerce(item, raw)
            old = current.get(key)
            if key == "sec.passcode" and raw != MASK:
                plain = str(value or "")
                legacy = bool(old) and not str(old).startswith(HASH_PREFIX)
                if plain and passcode_matches(old, plain) and not legacy:
                    continue
                value = encode_passcode(plain)
            if value == old:
                continue
            # 敏感项打码值原样回传时不当作修改
            if item.sensitive and raw == MASK:
                continue
            changed.append(key)
            if item.effect == "service":
                need_service.append(key)
            elif item.effect == "device":
                need_device.append(key)
            entries.append((key, item.sensitive, old, value))

        if not entries:
            return changed, need_service, need_device

        def _write(conn):
            stamp = _now()
            for key, sensitive, old, value in entries:
                conn.execute(
                    "INSERT INTO setting(key, value, updated_at) VALUES(?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, json.dumps(value, ensure_ascii=False), stamp),
                )
                conn.execute(
                    "INSERT INTO setting_change(key, is_sensitive, old_value, new_value, changed_at)"
                    " VALUES(?, ?, ?, ?, ?)",
                    (
                        key,
                        1 if sensitive else 0,
                        None if sensitive else json.dumps(old, ensure_ascii=False),
                        None if sensitive else json.dumps(value, ensure_ascii=False),
                        stamp,
                    ),
                )

        self.db.write(_write)
        self._cache = None
        return changed, need_service, need_device

    def history(self, limit=100):
        rows = self.db.conn.execute(
            "SELECT id, key, is_sensitive, old_value, new_value, changed_at"
            " FROM setting_change ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "key": row["key"],
                "sensitive": bool(row["is_sensitive"]),
                "old": row["old_value"],
                "new": row["new_value"],
                "at": row["changed_at"],
            }
            for row in rows
        ]

    def exportable(self):
        """导出包里的配置：敏感项一律不出现。"""
        return {
            key: value
            for key, value in self.all().items()
            if not BY_KEY[key].sensitive
        }

    @staticmethod
    def _coerce(item, raw):
        if item.kind == "switch":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "on", "yes"}
        if item.kind == "number":
            return float(raw) if isinstance(raw, str) else raw
        if item.kind == "select":
            text = str(raw)
            if item.choices and text not in item.choices:
                raise ValueError(f"{item.key} 只能是 {item.choices} 之一")
            return text
        return "" if raw is None else str(raw)
