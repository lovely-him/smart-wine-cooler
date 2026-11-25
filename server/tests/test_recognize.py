"""AI 补全：只出空字段的建议、不覆盖已填、没配置时不拖累任何链路。"""

import json

import pytest

from server.db import Database
from server.infer import recognize
from server.models.fields import ASKABLE, DETAIL_FIELDS

ASKABLE_KEYS = {item.key for item in ASKABLE}
FORM_KEYS = {item.key for item in DETAIL_FIELDS}


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    yield database
    database.close()


def suggest(**kwargs):
    defaults = {
        "labelText": "",
        "name": "",
        "suggestions": {},
        "conflicts": [],
        "uncertain": [],
        "error": "",
    }
    return {**defaults, **kwargs}


def test_prompt_carries_known_values_as_anchor():
    prompt = recognize.build_prompt({"名称": "示例商品", "brand": "某牌"}, ["producer", "region"])
    assert "示例商品" in prompt
    assert "某牌" in prompt
    assert "producer" in prompt
    assert "region" in prompt


def test_prompt_keeps_filled_fields_out_of_targets():
    prompt = recognize.build_prompt({"brand": "某牌"}, ["region"])
    assert "需要你补全的字段" in prompt
    assert "region" in prompt.split("需要你补全的字段")[1]
    assert "producer" not in prompt.split("需要你补全的字段")[1]


def test_prompt_says_no_known_info_when_empty():
    prompt = recognize.build_prompt({}, ["brand"])
    assert "没有任何已知信息" in prompt


def test_parse_clean_json():
    content = json.dumps(
        {
            "labelText": "某牌 干红",
            "suggestions": {
                "producer": {"value": "某酒庄", "basis": "web"},
                "region": {"value": "波尔多", "basis": "label"},
            },
        },
        ensure_ascii=False,
    )
    result = recognize.parse_suggest(content, ["producer", "region", "grape"], {})
    assert result["labelText"] == "某牌 干红"
    assert result["suggestions"]["producer"] == {"value": "某酒庄", "basis": "web"}
    assert result["suggestions"]["region"] == {"value": "波尔多", "basis": "label"}
    assert "grape" not in result["suggestions"]


def test_parse_strips_fence_and_prose():
    content = '查到的结果：\n```json\n{"suggestions": {"brand": {"value": "某厂"}}}\n```\n以上。'
    result = recognize.parse_suggest(content, ["brand"], {})
    assert result["suggestions"] == {"brand": {"value": "某厂", "basis": "web"}}


def test_parse_garbage_returns_empty():
    assert recognize.parse_suggest("完全不是 JSON", ["brand"], {}) == suggest()


def test_parse_never_overwrites_known_values():
    content = json.dumps({"suggestions": {"brand": {"value": "新牌子"}}}, ensure_ascii=False)
    result = recognize.parse_suggest(content, ["brand"], {"brand": "已填的牌子"})
    assert result["suggestions"] == {}


def test_parse_ignores_blank_values():
    content = json.dumps({"suggestions": {"brand": {"value": "  "}}}, ensure_ascii=False)
    assert recognize.parse_suggest(content, ["brand"], {})["suggestions"] == {}


def test_parse_reports_conflicts_only_for_known_fields():
    content = json.dumps(
        {
            "suggestions": {},
            "conflicts": [
                {"key": "region", "known": "填错了", "found": "波尔多", "basis": "web"},
                {"key": "grape", "known": "", "found": "梅洛", "basis": "web"},
            ],
        },
        ensure_ascii=False,
    )
    result = recognize.parse_suggest(content, ["grape"], {"region": "朗格多克"})
    assert result["conflicts"] == [
        {"key": "region", "known": "朗格多克", "found": "波尔多", "basis": "web"}
    ]


def test_parse_keeps_uncertain_notes():
    content = json.dumps({"uncertain": ["条码糊了"]}, ensure_ascii=False)
    assert recognize.parse_suggest(content, ["barcode"], {})["uncertain"] == ["条码糊了"]


def test_targets_belong_to_form():
    prompt = recognize.build_prompt({}, list(recognize.ASKABLE_KEYS))
    for key in recognize.ASKABLE_KEYS:
        assert key in prompt
    assert "purchase" not in recognize.ASKABLE_KEYS
    assert "note" not in recognize.ASKABLE_KEYS
    assert set(recognize.ASKABLE_KEYS) <= FORM_KEYS


def test_unconfigured_recognizer_is_none():
    assert recognize.from_config({}) is None
    assert recognize.from_config({"ai.enabled": True}) is None
    assert recognize.from_config({"ai.enabled": False, "ai.endpoint": "x", "ai.key": "k"}) is None


def test_configured_recognizer_returned():
    recognizer = recognize.from_config(
        {
            "ai.enabled": True,
            "ai.endpoint": "https://example.com/v1",
            "ai.key": "k",
            "ai.model": "some-model",
        }
    )
    assert recognizer is not None
    assert recognizer.available() is True


def test_suggest_without_config_reports_error_not_raises():
    result = recognize.Recognizer({}).suggest([], {}, ["brand"])
    assert result["error"] == "未配置大模型"
    assert result["suggestions"] == {}


def test_usage_resets_next_day(db):
    assert recognize.read_usage(db, "2026-11-23") == 0
    assert recognize.bump_usage(db, "2026-11-23") == 1
    assert recognize.bump_usage(db, "2026-11-23") == 2
    assert recognize.read_usage(db, "2026-11-23") == 2
    assert recognize.read_usage(db, "2026-11-24") == 0


def test_usage_resets_next_day(db):
    assert recognize.read_usage(db, "2026-11-23") == 0
    assert recognize.bump_usage(db, "2026-11-23") == 1
    assert recognize.bump_usage(db, "2026-11-23") == 2
    assert recognize.read_usage(db, "2026-11-23") == 2
    assert recognize.read_usage(db, "2026-11-24") == 0
