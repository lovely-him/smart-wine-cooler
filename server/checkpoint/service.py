"""提交：把编辑草稿落成新的核对状态。

起点比最新成立的观察旧时不是错误，是需要人裁决的冲突——所以它带上了是哪一次观察。
"""

from server.platform.db import checkpoints
from server.checkpoint import commit as commit_pipeline

from server.kernel.errors import BadRequest, Conflict
from server.platform.views import as_checkpoint


class CheckpointService:
    def __init__(self, db, image_dir, commit):
        self._db = db
        self._image_dir = image_dir
        self._commit = commit

    def create(self, body):
        try:
            checkpoint_id = self._commit(body)
        except commit_pipeline.ConflictError as exc:
            raise Conflict(
                {
                    "message": "期间来过新的观察",
                    "latestObservationId": exc.latest_observation_id,
                }
            ) from exc
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return as_checkpoint(self._image_dir, self._find(checkpoint_id))

    def _find(self, checkpoint_id):
        for item in checkpoints.list_checkpoints(self._db, limit=200):
            if item["id"] == checkpoint_id:
                return item
        return None
