"""详情字段：全局模板与单类别扩展两级并存，同 key 以类别自己的为准。"""

import pytest

from server.db import Database, fields, repo


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    fields.ensure_seeded(database)
    yield database
    database.close()


def test_seed_once_and_keep_edits(db):
    assert fields.ensure_seeded(db) == 0
    assert len(fields.list_fields(db)) == 19


def test_category_field_overrides_global_label(db):
    category_id = repo.create_category(db, "甲")
    fields.create_field(db, "vintage", "年份（这瓶特殊）", True, category_id)

    merged = {item["key"]: item for item in fields.list_fields(db, category_id)}
    assert merged["vintage"]["label"] == "年份（这瓶特殊）"
    assert merged["vintage"]["categoryId"] == category_id

    global_only = {item["key"]: item for item in fields.list_fields(db)}
    assert global_only["vintage"]["label"] == "年份"


def test_category_can_add_own_field(db):
    category_id = repo.create_category(db, "甲")
    fields.create_field(db, "刻字", "瓶身刻字", False, category_id)

    assert "刻字" in {item["key"] for item in fields.list_fields(db, category_id)}
    assert "刻字" not in {item["key"] for item in fields.list_fields(db)}


def test_same_key_twice_in_one_scope_is_rejected(db):
    category_id = repo.create_category(db, "甲")

    fields.create_field(db, "brand", "品牌二号", True, category_id)
    with pytest.raises(fields.KeyTaken):
        fields.create_field(db, "brand", "品牌三号", True, category_id)


def test_update_and_delete(db):
    field_id = fields.create_field(db, "刻字", "瓶身刻字", True)
    fields.update_field(db, field_id, "刻字内容", False)

    saved = fields.get_field(db, field_id)
    assert saved["label"] == "刻字内容"
    assert saved["askable"] is False

    fields.delete_field(db, field_id)
    assert fields.get_field(db, field_id) is None


def test_deleting_category_drops_its_fields(db):
    category_id = repo.create_category(db, "甲")
    fields.create_field(db, "刻字", "瓶身刻字", True, category_id)

    repo.delete_category(db, category_id)

    assert all(item["categoryId"] is None for item in fields.list_fields(db))
