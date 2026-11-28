"""运行日志缓冲：默认把缓冲里的一股脑给出来，limit 只是从尾部裁一刀。

为什么盯这条：条数上限只该有一处定义（缓冲的 CAPACITY）。接口默认值或界面
再写一个数字，就会出现「界面写着 150、实际只返 120」这种自相矛盾。
"""

from collections import deque

from server.platform import logs


def test_recent_returns_whole_buffer_by_default(monkeypatch):
    monkeypatch.setattr(logs, "_buffer", deque([{"at": str(i)} for i in range(5)], maxlen=5))
    assert len(logs.recent()) == 5


def test_recent_clips_from_the_tail(monkeypatch):
    monkeypatch.setattr(logs, "_buffer", deque([{"at": str(i)} for i in range(5)], maxlen=5))
    assert [item["at"] for item in logs.recent(2)] == ["3", "4"]


def test_recent_never_exceeds_what_is_buffered(monkeypatch):
    monkeypatch.setattr(logs, "_buffer", deque([{"at": "0"}], maxlen=5))
    assert len(logs.recent(10)) == 1
