"""类别：改名、合并、删除，以及历史随访到合并后的目标。"""

import numpy as np
import pytest

from server.platform.db import categories, checkpoints, Database, observations, references


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    yield database
    database.close()


def feature(seed=1):
    return np.full(8, seed, dtype=np.float32) / np.sqrt(8 * seed)


def test_update_category_changes_name_shape_and_details(db):
    category_id = categories.create_category(db, "甲", "bordeaux", {"brand": "A"})

    categories.update_category(db, category_id, "乙", "burgundy", {"brand": "B"})

    item = categories.list_categories(db)[0]
    assert item["name"] == "乙"
    assert item["shape"] == "burgundy"
    assert item["details"] == {"brand": "B"}


def test_delete_category_removes_references_and_reports_paths(db):
    category_id = categories.create_category(db, "甲")
    references.add_reference(db, category_id, "1/a.jpg", feature(1), 8)
    references.add_reference(db, category_id, "1/b.jpg", feature(2), 8)

    paths = categories.delete_category(db, category_id)

    assert sorted(paths) == ["1/a.jpg", "1/b.jpg"]
    assert categories.list_categories(db) == []
    assert references.reference_groups(db) == []


def test_merge_moves_references_to_target_and_leaves_alias(db):
    source = categories.create_category(db, "甲")
    target = categories.create_category(db, "乙")
    references.add_reference(db, source, "1/a.jpg", feature(1), 8)

    categories.merge_categories(db, source, target)

    left = categories.list_categories(db)
    assert [item["name"] for item in left] == ["乙"]
    assert len(left[0]["refs"]) == 1
    assert categories.category_aliases(db) == {str(source): target}


def test_alias_follows_chain_to_final_target(db):
    first = categories.create_category(db, "A")
    middle = categories.create_category(db, "B")
    last = categories.create_category(db, "C")

    categories.merge_categories(db, first, middle)
    categories.merge_categories(db, middle, last)

    assert categories.category_aliases(db) == {str(first): last, str(middle): last}


def test_alias_dropped_when_target_deleted_too(db):
    source = categories.create_category(db, "A")
    target = categories.create_category(db, "B")
    categories.merge_categories(db, source, target)

    categories.delete_category(db, target)

    assert categories.category_aliases(db) == {}


def test_delete_reference_returns_path_once(db):
    category_id = categories.create_category(db, "甲")
    reference_id = references.add_reference(db, category_id, "1/a.jpg", feature(1), 8)

    assert references.delete_reference(db, reference_id) == "1/a.jpg"
    assert references.delete_reference(db, reference_id) is None
    assert references.reference_groups(db) == []


def test_duplicate_name_is_reported(db):
    categories.create_category(db, "甲")

    with pytest.raises(categories.NameTaken):
        categories.create_category(db, "甲")


def test_rename_to_existing_name_is_reported(db):
    categories.create_category(db, "甲")
    other = categories.create_category(db, "乙")

    with pytest.raises(categories.NameTaken):
        categories.update_category(db, other, "甲", "", {})


def test_category_shape_rejects_unknown_value():
    from pydantic import ValidationError

    from server.api.schemas import CategoryInput

    assert CategoryInput(name="甲", shape="bordeaux").shape == "bordeaux"
    with pytest.raises(ValidationError):
        CategoryInput(name="甲", shape="波尔多瓶")


def test_category_usage_counts_history_and_confirmations(db):
    kept = categories.create_category(db, "甲")
    dropped = categories.create_category(db, "乙")
    observation_id = observations.save_observation(
        db, status="done", trigger="manual", started_at="2026-11-22T10:00:00+08:00"
    )
    layer = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    observations.save_layer_record(
        db,
        observation_id,
        "L1",
        "第一层",
        True,
        "",
        "1/a.jpg",
        [
            {"id": f"{observation_id}-L1-{index}", "box": layer, "category_id": category_id}
            for index, category_id in enumerate((kept, dropped, kept))
        ],
    )
    checkpoints.save_checkpoint(
        db,
        based_on_observation=observation_id,
        reported=[],
        submitted=[{"layerId": "L1", "imageUrl": "1/a.jpg", "entities": [{"categoryId": kept}]}],
        note="",
    )

    assert categories.category_usage(db, kept) == 3
    assert categories.category_usage(db, dropped) == 1
    assert categories.category_usage(db, 9999) == 0
