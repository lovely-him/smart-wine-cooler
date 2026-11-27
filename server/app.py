"""FastAPI 应用装配：路由 + 触发调度 + 前端产物。

服务是单进程多线程：Web 应答与观察分在不同的执行单元里，一次观察要跑好几秒，
放在事件循环里界面会全程无响应。
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.api.preview import build_preview_router
from server.api.routes import build_router
from server.config import bootstrap
from server.hardware.door import load_door
from server.pipeline.scheduler import Scheduler
from server.runtime import Runtime

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(bootstrap.ROOT) / "frontend/dist-standalone/wine-cooler"


def build_door(config):
    if not config.get("trig.door_enabled"):
        return None
    gpio = str(config.get("trig.door_gpio") or "").strip()
    if not gpio:
        return load_door({"backend": "mock"})
    try:
        return load_door(
            {
                "backend": "gpio",
                "pin": int(gpio),
                "closed_level": str(config.get("trig.door_closed_level") or "low"),
            }
        )
    except Exception as exc:
        logger.warning("门磁打开失败，按门磁没接处理：%s", exc)
        return None



def create_app(runtime=None, start_scheduler=True):
    rt = runtime or Runtime()
    app = FastAPI(title="wine-cooler")
    app.state.runtime = rt
    app.include_router(build_router(rt))
    app.include_router(build_preview_router(rt))

    if start_scheduler:
        door = build_door(rt.config)
        rt.door = door
        rt.sync_light()
        scheduler = Scheduler(rt, door)
        rt.cancel_check = scheduler.cancelled
        scheduler.start()
        app.state.scheduler = scheduler

    index = FRONTEND_DIST / "wine-cooler.html"
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def ui():
            return FileResponse(index)

    return app
