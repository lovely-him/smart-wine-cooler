"""运行日志的在内存里的一份副本。

界面是事后看的窗口，但排错要当场看：日志只进 journald 的话，
用户不开终端就永远看不到"这次为什么没认出来"。

所以留最近若干条在内存里，重启即清空——它是"这次的运行状态"，不是审计记录。
审计（谁改了什么配置）另有 setting_change 表。
"""

import logging
import threading
from collections import deque
from datetime import datetime

CAPACITY = 400

_buffer = deque(maxlen=CAPACITY)
_lock = threading.Lock()


NOISY = ("uvicorn.access",)

LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _BufferHandler(logging.Handler):
    def emit(self, record):
        if record.name in NOISY:
            return
        try:
            message = record.getMessage()
        except Exception:
            return
        entry = {
            "at": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "name": record.name,
            "message": message,
        }
        with _lock:
            _buffer.append(entry)


def install():
    root = logging.getLogger()
    if any(isinstance(handler, _BufferHandler) for handler in root.handlers):
        return
    root.addHandler(_BufferHandler())


def set_level(name):
    """级别传整数不传名字：rknnlite 的 C 扩展会把 logging 的名字表换成单字母。"""
    level = LEVELS.get(str(name or "info").lower(), logging.INFO)
    logging.getLogger().setLevel(level)
    for logger_name in UVICORN_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)


def recent(limit=None):
    with _lock:
        items = list(_buffer)
    return items[-(limit or CAPACITY) :]


def clear():
    with _lock:
        _buffer.clear()
