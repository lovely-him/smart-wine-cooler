"""读模型：界面要的快照与历史分页。

只读，不写库也不发通知。界面的「现在是什么」全部从这里取，别处不再各拼一份。
"""

import shutil

from server.platform.db import categories, checkpoints, history, observations, sequence

from server.kernel.errors import BadRequest
from server.platform.views import as_category, as_change, as_checkpoint, as_observation


class OverviewService:
    def __init__(self, db, image_dir, layers, live_status):
        self._db = db
        self._image_dir = image_dir
        self._layers = layers
        self._live = live_status

    def _observation(self, row):
        return as_observation(self._image_dir, row)

    def _checkpoint(self, row):
        return as_checkpoint(self._image_dir, row)

    def snapshot(self, recent=20):
        db = self._db
        return {
            "sequence": sequence.current(db.conn),
            "layers": [{"id": layer.id, "name": layer.name} for layer in self._layers()],
            "observations": [
                self._observation(item) for item in observations.list_observations(db, limit=recent)
            ],
            "checkpoints": [
                self._checkpoint(item) for item in checkpoints.list_checkpoints(db, limit=recent)
            ],
            "categories": [as_category(item) for item in categories.list_categories(db)],
            "category_aliases": categories.category_aliases(db),
            "changes": [as_change(item) for item in checkpoints.list_changes(db, limit=recent)],
            "live": self._live(),
        }

    def history(self, before=None, limit=20):
        db = self._db
        observation_items = [
            self._observation(item)
            for item in observations.list_observations(db, before_id=before, limit=limit)
        ]
        checkpoint_items = [
            self._checkpoint(item)
            for item in checkpoints.list_checkpoints(db, before_id=before, limit=limit)
        ]
        changes = [
            as_change(item) for item in checkpoints.list_changes(db, before_id=before, limit=limit)
        ]

        merged = [(item["id"], "observation") for item in observation_items]
        merged += [(item["id"], "checkpoint") for item in checkpoint_items]
        merged += [(item["id"], "change") for item in changes]
        merged.sort(reverse=True)
        page = merged[:limit]
        keep = {item[0] for item in page}

        return {
            "observations": [item for item in observation_items if item["id"] in keep],
            "checkpoints": [item for item in checkpoint_items if item["id"] in keep],
            "changes": [item for item in changes if item["id"] in keep],
            "next_before_id": page[-1][0] if len(page) == limit else None,
        }

    def observations(self, before=None, limit=20):
        items = observations.list_observations(self._db, before_id=before, limit=limit)
        return {
            "items": [self._observation(item) for item in items],
            "next_before_id": items[-1]["id"] if len(items) == limit else None,
        }

    def changes(self, before=None, limit=20):
        items = checkpoints.list_changes(self._db, before_id=before, limit=limit)
        return {
            "items": [as_change(item) for item in items],
            "next_before_id": items[-1]["id"] if len(items) == limit else None,
        }

    def position(self, layer_id, index):
        return {
            "layerId": layer_id,
            "index": index,
            "entries": checkpoints.position_history(self._db, layer_id, index),
        }

    def delete_history(self, ids):
        observation_ids, checkpoint_ids, unknown = history.classify(self._db, ids)
        if unknown:
            raise BadRequest(
                f"这些记录不能单独删（事件随观察一起删）：{', '.join(str(i) for i in unknown)}"
            )
        observations.delete_observations(self._db, observation_ids)
        checkpoints.delete_checkpoints(self._db, checkpoint_ids)
        for observation_id in observation_ids:
            shutil.rmtree(self._image_dir / str(observation_id), ignore_errors=True)
