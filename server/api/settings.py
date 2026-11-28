"""设置、配网、维护、日志、设备与柜层列表。"""

from fastapi import APIRouter

from server.api.schemas import MaintenanceRequest, NetworkSwitch, SaveSettingsRequest
from server.kernel.objects import (
    CameraListOut,
    LayerListOut,
    NetworkState,
    SaveSettingsResult,
    SettingsState,
)
from server.settings.service import SettingsService


def build(settings: SettingsService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings", response_model=SettingsState)
    def state():
        return settings.state()

    @router.post("/api/settings", response_model=SaveSettingsResult)
    def save(body: SaveSettingsRequest):
        return settings.save(body.group, body.values, body.passcode)

    @router.get("/api/network", response_model=NetworkState)
    def network_state():
        return settings.network()

    @router.post("/api/network", response_model=NetworkState)
    def network_switch(body: NetworkSwitch):
        return settings.switch_network(body.ssid, body.password, body.passcode)

    @router.post("/api/maintenance")
    def run_maintenance(body: MaintenanceRequest):
        return settings.maintenance(body.action, body.passcode)

    @router.get("/api/logs")
    def runtime_logs(limit: int | None = None):
        return settings.runtime_logs(limit)

    @router.get("/api/cameras", response_model=CameraListOut)
    def cameras():
        return settings.cameras()

    @router.get("/api/layers", response_model=LayerListOut)
    def layers():
        return settings.layers()

    return router
