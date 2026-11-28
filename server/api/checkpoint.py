"""提交：把编辑草稿落成新的核对状态。"""

from fastapi import APIRouter

from server.api.schemas import CommitRequest
from server.checkpoint.service import CheckpointService


def build(checkpoint: CheckpointService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/checkpoints")
    def create(body: CommitRequest):
        return checkpoint.create(body)

    return router
