"""HTTP 层：按资源分文件，每个端点只做「解析请求 → 调一个 service → 交回结果」。

业务判断一律在各业务模块里；这里不查库、不做流程。
"""

from fastapi import APIRouter

from . import catalog, checkpoint, events, history, media, observe, settings, snapshot


def build_router(
    *,
    overview,
    observe_service,
    checkpoint_service,
    catalog_service,
    settings_service,
    bus,
    image_dir,
    reference_dir,
) -> APIRouter:
    router = APIRouter()
    router.include_router(snapshot.build(overview))
    router.include_router(history.build(overview))
    router.include_router(observe.build(observe_service))
    router.include_router(checkpoint.build(checkpoint_service))
    router.include_router(catalog.build(catalog_service))
    router.include_router(settings.build(settings_service))
    router.include_router(events.build(bus))
    router.include_router(media.build(image_dir, reference_dir))
    return router
