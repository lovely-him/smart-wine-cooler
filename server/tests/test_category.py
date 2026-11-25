"""类别：改名、合并、删除，以及历史随访到合并后的目标。"""

import numpy as np
import pytest

from server.db import Database, checkpoints, repo


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db")
    database.init_schema()
    yield database
    database.close()


def feature(seed=1):
    return np.full(8, seed, dtype=np.float32) / np.sqrt(8 * seed)


def test_update_category_changes_name_shape_and_details(db):
    category_id = repo.create_category(db, "甲", "bordeaux", {"brand": "A"})

    repo.update_category(db, category_id, "乙", "burgundy", {"brand": "B"})

    item = repo.list_categories(db)[0]
    assert item["name"] == "乙"
    assert item["shape"] == "burgundy"
    assert item["details"] == {"brand": "B"}


def test_delete_category_removes_references_and_reports_paths(db):
    category_id = repo.create_category(db, "甲")
    repo.add_reference(db, category_id, "1/a.jpg", feature(1), 8)
    repo.add_reference(db, category_id, "1/b.jpg", feature(2), 8)

    paths = repo.delete_category(db, category_id)

    assert sorted(paths) == ["1/a.jpg", "1/b.jpg"]
    assert repo.list_categories(db) == []
    assert repo.reference_groups(db) == []


def test_merge_moves_references_to_target_and_leaves_alias(db):
    source = repo.create_category(db, "甲")
    target = repo.create_category(db, "乙")
    repo.add_reference(db, source, "1/a.jpg", feature(1), 8)

    repo.merge_categories(db, source, target)

    left = repo.list_categories(db)
    assert [item["name"] for item in left] == ["乙"]
    assert len(left[0]["refs"]) == 1
    assert repo.category_aliases(db) == {str(source): target}


def test_alias_follows_chain_to_final_target(db):
    first = repo.create_category(db, "A")
    middle = repo.create_category(db, "B")
    last = repo.create_category(db, "C")

    repo.merge_categories(db, first, middle)
    repo.merge_categories(db, middle, last)

    assert repo.category_aliases(db) == {str(first): last, str(middle): last}


def test_alias_dropped_when_target_deleted_too(db):
    source = repo.create_category(db, "A")
    target = repo.create_category(db, "B")
    repo.merge_categories(db, source, target)

    repo.delete_category(db, target)

    assert repo.category_aliases(db) == {}


def test_delete_reference_returns_path_once(db):
    category_id = repo.create_category(db, "甲")
    reference_id = repo.add_reference(db, category_id, "1/a.jpg", feature(1), 8)

    assert repo.delete_reference(db, reference_id) == "1/a.jpg"
    assert repo.delete_reference(db, reference_id) is None
    assert repo.reference_groups(db) == []


def test_duplicate_name_is_reported(db):
    repo.create_category(db, "甲")

    with pytest.raises(repo.NameTaken):
        repo.create_category(db, "甲")


def test_rename_to_existing_name_is_reported(db):
    repo.create_category(db, "甲")
    other = repo.create_category(db, "乙")

    with pytest.raises(repo.NameTaken):
        repo.update_category(db, other, "甲", "", {})


def test_category_shape_rejects_unknown_value():
    from pydantic import ValidationError

    from server.models import CategoryInput

    assert CategoryInput(name="甲", shape="bordeaux").shape == "bordeaux"
    with pytest.raises(ValidationError):
        CategoryInput(name="甲", shape="波尔多瓶")


def test_category_usage_counts_history_and_confirmations(db):
    kept = repo.create_category(db, "甲")
    dropped = repo.create_category(db, "乙")
    observation_id = repo.save_observation(
        db, status="done", trigger="manual", started_at="2026-11-22T10:00:00+08:00"
    )
    layer = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    repo.save_layer_record(
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

    assert repo.category_usage(db, kept) == 3
    assert repo.category_usage(db, dropped) == 1
    assert repo.category_usage(db, 9999) == 0
