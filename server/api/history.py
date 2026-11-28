"""历史：观察、确认、变化三类记录合成一条时间线，以及层内位置历史。"""

from fastapi import APIRouter, Query

from server.api.schemas import HistoryDelete
from server.kernel.objects import HistoryPage
from server.checkpoint.queries import OverviewService


def build(overview: OverviewService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/history", response_model=HistoryPage)
    def history(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        return overview.history(before, limit)

    @router.delete("/api/history", status_code=204)
    def delete_history(body: HistoryDelete):
        overview.delete_history(body.ids)

    @router.get("/api/positions/{layer_id}/{index}")
    def position(layer_id: str, index: int):
        return overview.position(layer_id, index)

    return router
