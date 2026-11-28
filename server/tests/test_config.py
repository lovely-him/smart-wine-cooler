"""配置：出厂默认值、改动持久化、敏感项不外泄。"""

import sqlite3

import pytest

from server.platform.settings_def import DEFAULTS
from server.settings.store import ConfigStore
from server.settings.store import MASK
from server.platform.db import Database


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    database = Database(path)
    database.init_schema()
    yield database
    database.close()


def reopen(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    return database


def test_defaults_available(db):
    store = ConfigStore(db)
    assert store.get("judge.conf") == DEFAULTS["judge.conf"]
    assert store.get("cam.cameras") == DEFAULTS["cam.cameras"]
    assert store.get("cam.layers") == DEFAULTS["cam.layers"]


def test_change_survives_reopen(tmp_path, db):
    ConfigStore(db).set_many("judge", {"judge.conf": 0.42})
    db.close()

    other = reopen(tmp_path)
    try:
        assert ConfigStore(other).get("judge.conf") == 0.42
    finally:
        other.close()


def test_change_reported_by_effect(db):
    store = ConfigStore(db)
    _, _, need_device = store.set_many("sys", {"sys.autostart": False})
    assert need_device == ["sys.autostart"]

    _, need_service, _ = store.set_many("cam", {"cam.batch": 8})
    assert need_service == []


def test_noop_write_reports_nothing(db):
    store = ConfigStore(db)
    changed, _, _ = store.set_many("judge", {"judge.conf": DEFAULTS["judge.conf"]})
    assert changed == []


def test_sensitive_never_in_change_log(db):
    store = ConfigStore(db)
    store.set_many("ai", {"ai.key": "sk-secret"})
    history = store.history()
    assert history[0]["key"] == "ai.key"
    assert history[0]["sensitive"] is True
    assert history[0]["old"] is None
    assert history[0]["new"] is None


def test_sensitive_masked_when_read(db):
    store = ConfigStore(db)
    store.set_many("ai", {"ai.key": "sk-secret"})
    values = {
        control["key"]: control["value"]
        for group in store.groups()
        for item in group["items"]
        for control in item["controls"]
    }
    assert values["ai.key"] == MASK
    assert store.get("ai.key") == "sk-secret"


def test_masked_value_echo_is_not_a_change(db):
    store = ConfigStore(db)
    store.set_many("ai", {"ai.key": "sk-secret"})
    changed, _, _ = store.set_many("ai", {"ai.key": MASK})
    assert changed == []
    assert store.get("ai.key") == "sk-secret"


def test_export_excludes_sensitive(db):
    store = ConfigStore(db)
    store.set_many("ai", {"ai.key": "sk-secret"})
    exported = store.exportable()
    assert "ai.key" not in exported
    assert "judge.conf" in exported


def test_unknown_key_rejected(db):
    store = ConfigStore(db)
    with pytest.raises(KeyError):
        store.set_many("judge", {"judge.nope": 1})
    with pytest.raises(KeyError):
        store.set_many("judge", {"cam.batch": 8})


def test_select_rejects_out_of_range(db):
    store = ConfigStore(db)
    with pytest.raises(ValueError):
        store.set_many("sys", {"sys.log_level": "verbose"})


def test_switch_coercion(db):
    store = ConfigStore(db)
    store.set_many("trig", {"trig.door_enabled": "true"})
    assert store.get("trig.door_enabled") is True
    store.set_many("trig", {"trig.door_enabled": "off"})
    assert store.get("trig.door_enabled") is False


def test_items_cover_every_setting(tmp_path):
    """每个设置键恰好属于一个配置项，界面上不会漏掉可改的东西。"""
    from server.platform.settings_def import DEFAULTS, ITEMS

    seen = {}
    for item in ITEMS:
        for key in item.keys:
            assert key not in seen, f"{key} 同时属于 {seen[key]} 与 {item.id}"
            seen[key] = item.id

    missing = sorted(set(DEFAULTS) - set(seen))
    assert missing == [], f"这些设置没有对应的配置项：{missing}"


def test_item_ids_unique():
    from server.platform.settings_def import ITEMS, ITEMS_BY_ID

    ids = [item.id for item in ITEMS]
    assert len(ids) == len(set(ids))
    assert len(ITEMS_BY_ID) == len(ITEMS)


def test_items_cover_every_setting_key():
    from server.platform.settings_def import ITEMS, SETTINGS

    covered = [key for item in ITEMS for key in item.keys]
    assert len(covered) == len(set(covered))
    assert set(covered) == {item.key for item in SETTINGS}


def test_state_groups_carry_controls_and_values(db):
    store = ConfigStore(db)
    state = store.state()

    group_ids = [group["id"] for group in state["groups"]]
    assert group_ids == ["net", "cam", "trig", "judge", "ai", "data", "sec", "sys"]

    judge = next(group for group in state["groups"] if group["id"] == "judge")
    tau = next(item for item in judge["items"] if item["id"] == "JUDGE-02")
    assert tau["effect"] == "now"
    assert [control["key"] for control in tau["controls"]] == ["judge.tau", "judge.margin"]


def test_dangerous_item_needs_passcode(db):
    store = ConfigStore(db)
    layers = DEFAULTS["cam.layers"]

    with pytest.raises(PermissionError):
        store.guard({"cam.layers": layers}, "")

    store.guard({"sec.passcode": "1234", "sec.passcode_hint": "门牌号"}, "")
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    with pytest.raises(PermissionError):
        store.guard({"cam.layers": layers}, "0000")
    store.guard({"cam.layers": layers}, "1234")
    store.guard({"judge.tau": 0.7}, "")


def test_passcode_needs_hint(db):
    store = ConfigStore(db)

    with pytest.raises(ValueError):
        store.guard({"sec.passcode": "1234"}, "")

    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    with pytest.raises(ValueError):
        store.guard({"sec.passcode": "5678", "sec.passcode_hint": "  "}, "1234")
    store.guard({"sec.passcode": "5678", "sec.passcode_hint": "新提示"}, "1234")
    store.guard({"sec.passcode": "********", "sec.passcode_hint": "新提示"}, "1234")

    store.set_many("sec", {"sec.passcode": "", "sec.passcode_hint": "随便"})
    assert store.get("sec.passcode") == ""
    assert store.get("sec.passcode_hint") == ""


def test_passcode_stored_as_salted_digest(db):
    store = ConfigStore(db)
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    first = store.get("sec.passcode")

    assert first.startswith("sha256$")
    assert "1234" not in first
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    assert store.get("sec.passcode") == first

    store.set_many("sec", {"sec.passcode": "5678", "sec.passcode_hint": "门牌号"})
    second = store.get("sec.passcode")
    assert second != first
    assert store.check_passcode("5678")
    assert not store.check_passcode("1234")


def test_plaintext_passcode_is_never_accepted(db):
    def seed(conn):
        conn.execute(
            "INSERT INTO setting(key, value, updated_at) VALUES('sec.passcode', ?, '')",
            ('"1234"',),
        )

    db.write(seed)

    store = ConfigStore(db)
    assert not store.check_passcode("1234")
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    assert store.get("sec.passcode").startswith("sha256$")
    assert store.check_passcode("1234")


def test_export_drops_sensitive_settings(db, tmp_path):
    from server.settings.maintenance import _drop_sensitive

    store = ConfigStore(db)
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "门牌号"})
    store.set_many("ai", {"ai.key": "sk-test"})

    snapshot = tmp_path / "snap.db"
    db.conn.execute("VACUUM INTO ?", (str(snapshot),))
    _drop_sensitive(snapshot)

    conn = sqlite3.connect(snapshot)
    try:
        keys = {row[0] for row in conn.execute("SELECT key FROM setting")}
    finally:
        conn.close()

    assert "sec.passcode" not in keys
    assert "ai.key" not in keys
    assert "sec.passcode_hint" in keys


def test_state_changes_name_the_item(db):
    store = ConfigStore(db)
    store.set_many("judge", {"judge.tau": 0.72})

    change = store.state()["changes"][0]
    assert change["itemId"] == "JUDGE-02"
    assert change["note"] == "0.68 → 0.72"


def test_changing_passcode_keeps_hint(db):
    store = ConfigStore(db)
    store.set_many("sec", {"sec.passcode": "1234", "sec.passcode_hint": "柜子在阳台"})

    store.guard({"sec.passcode": "5678"}, "1234")
    store.set_many("sec", {"sec.passcode": "5678"})

    assert store.get("sec.passcode_hint") == "柜子在阳台"
    assert store.check_passcode("5678")
