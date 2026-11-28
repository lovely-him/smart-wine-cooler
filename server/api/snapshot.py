"""快照与增量列表：界面每次刷新拉快照，翻页走 observations / changes。"""

from fastapi import APIRouter, Query

from server.kernel.objects import Snapshot
from server.checkpoint.queries import OverviewService


def build(overview: OverviewService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/snapshot", response_model=Snapshot)
    def snapshot(recent: int = Query(default=20, ge=0, le=200)):
        return overview.snapshot(recent)

    @router.get("/api/observations")
    def observations(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        return overview.observations(before, limit)

    @router.get("/api/changes")
    def changes(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        return overview.changes(before, limit)

    return router
