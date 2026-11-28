"""观察相关：人工触发、零档案分组、柜内灯、识别进度。"""

from fastapi import APIRouter

from server.api.schemas import LightRequest
from server.observe.service import ObserveService


def build(observe: ObserveService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/observations", status_code=202)
    def trigger():
        return observe.trigger()

    @router.get("/api/observations/{observation_id}/groups")
    def groups(observation_id: int):
        return observe.unknown_groups(observation_id)

    @router.put("/api/light")
    def set_light(body: LightRequest):
        return observe.set_light(body.on)

    @router.get("/api/ai/status")
    def ai_status():
        return observe.recognition()

    return router
