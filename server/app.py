"""FastAPI 应用装配：路由 + 触发调度 + 前端产物。

这里是唯一的组合根——只有它允许把具体实现塞给 service。
服务是单进程多线程：Web 应答与观察分在不同的执行单元里，一次观察要跑好几秒，
放在事件循环里界面会全程无响应。
"""

import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.platform import logs
from server.settings import maintenance
from server.api import build_router
from server.api.errors import install_error_handlers
from server.api.preview import build_preview_router
from server.catalog.advisor import Advisor
from server.catalog.service import CatalogService
from server.checkpoint.queries import OverviewService
from server.checkpoint.service import CheckpointService
from server.platform import bootstrap
from server.settings.store import ConfigStore
from server.platform.db import Database, fields as field_store, observations
from server.platform.events import EventBus
from server.platform import storage
from server.platform.door import load_door
from server.observe.runner import ObserveRunner
from server.observe.service import ObserveService
from server.checkpoint import commit as commit_pipeline
from server.catalog import grouping
from server.observe import scheduler
from server.observe.scheduler import Scheduler
from server.platform import status
from server.platform.hardware import Hardware
from server.platform.housekeeping import Housekeeping
from server.settings.applier import SettingsApplier
from server.settings.service import SettingsService
from server.vision import probe
from server.vision.engines import Engines
from server.vision.plan import CapturePlan

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


def create_app(start_scheduler=True):
    db = Database(bootstrap.DB_PATH)
    db.init_schema()
    field_store.ensure_seeded(db)
    config = ConfigStore(db)
    image_dir = Path(bootstrap.IMAGE_DIR)
    reference_dir = Path(bootstrap.REFERENCE_DIR)
    storage.ensure_dir(image_dir, reference_dir)
    bus = EventBus()
    plan = CapturePlan(config)
    engines = Engines(db, config)
    housekeeping = Housekeeping(db, image_dir, config)
    hardware = Hardware(config, bus)
    interrupted = threading.Event()
    observe = ObserveRunner(
        db=db,
        image_dir=image_dir,
        plan=plan,
        engines=engines,
        housekeeping=housekeeping,
        bus=bus,
        sync_light=hardware.sync_light,
        interrupted=interrupted,
    )
    started_at = time.time()

    logs.set_level(config.get("sys.log_level"))
    dropped = housekeeping.reclaim_orphans()
    if dropped:
        logger.info("清掉 %d 个没记录指向的图片目录", dropped)

    advisor = Advisor(
        db=db,
        config=config,
        image_dir=image_dir,
        reference_dir=reference_dir,
    )
    applier = SettingsApplier(
        config=config,
        rebuild_light=hardware.rebuild_light,
        reload_recognizer=advisor.reload,
        reload_inference=engines.reload,
        is_observing=observe.is_observing,
    )

    app = FastAPI(title="wine-cooler")
    app.state.bus = bus
    install_error_handlers(app)
    app.include_router(
        build_router(
            overview=OverviewService(
                db=db,
                image_dir=image_dir,
                layers=lambda: plan.layers,
                live_status=lambda: status.live_status(
                    camera_status=lambda: probe.cameras(plan),
                    housekeeping=housekeeping,
                    hardware=hardware,
                    observe=observe,
                    started_at=started_at,
                ),
            ),
            observe_service=ObserveService(
                db=db,
                config=config,
                start_observe=observe.start_observe,
                manual_allowed=lambda: scheduler.manual_allowed(config),
                blocked_reason=lambda: scheduler.blocked_reason(hardware),
                set_light=hardware.set_light,
                recognition_status=advisor.status,
                unknown_groups=lambda observation, threshold: grouping.unknown_groups(
                    image_dir=image_dir,
                    engines=engines,
                    observation=observation,
                    threshold=threshold,
                ),
            ),
            checkpoint_service=CheckpointService(
                db=db,
                image_dir=image_dir,
                commit=lambda body: commit_pipeline.commit(
                    db=db,
                    image_dir=image_dir,
                    reference_dir=reference_dir,
                    config=config,
                    engines=engines,
                    body=body,
                ),
            ),
            catalog_service=CatalogService(
                db=db,
                reference_dir=reference_dir,
                reload_references=engines.reload_references,
                suggest=advisor.suggest,
            ),
            settings_service=SettingsService(
                config=config,
                apply_settings=applier.apply,
                layers=lambda: plan.layers,
                used_layers=lambda: observations.used_layers(db),
                run_maintenance=lambda action: maintenance.run(
                    action,
                    config=config,
                    db=db,
                    image_dir=image_dir,
                    reference_dir=reference_dir,
                ),
            ),
            bus=bus,
            image_dir=image_dir,
            reference_dir=reference_dir,
        )
    )
    app.include_router(build_preview_router(hardware, observe.is_observing))

    if start_scheduler:
        hardware.attach_door(build_door(config))
        hardware.sync_light(False)
        Scheduler(
            config=config,
            db=db,
            observe=observe,
            hardware=hardware,
            interrupted=interrupted,
        ).start()

    index = FRONTEND_DIST / "wine-cooler.html"
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def ui():
            return FileResponse(index)

    return app
