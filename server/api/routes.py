"""HTTP 接口：当前状态、观察历史、变化记录、图片文件，以及事件流。

`snapshot` 要小——界面每次刷新都会拉它；历史翻页走 `observations`。
形状与前端 `domain/types.ts` 一致，两边不再各写一套。
"""

import asyncio
import json
import shutil

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from server.db import checkpoints, repo, sequence
from server.hardware import network
from server.hardware.devices import list_cameras
from server.models import (
    CameraListOut,
    CategoryInput,
    CategoryUpdate,
    CommitRequest,
    FieldDef,
    FieldInput,
    FieldUpdate,
    NetworkState,
    NetworkSwitch,
    HistoryDelete,
    HistoryPage,
    LayerListOut,
    MergeRequest,
    LightRequest,
    MaintenanceRequest,
    SuggestRequest,
    SuggestResponse,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsState,
    Snapshot,
)
from server.db import fields as field_store
from server import maintenance
from server.pipeline import commit as commit_pipeline
from server import logs
from server.pipeline import grouping
from server.pipeline import scheduler

IMAGE_ROOT = "/img"
REFERENCE_ROOT = "/ref"


def _entity(row):
    return {
        "id": row["id"],
        "box": row["box"],
        "categoryId": row["category_id"],
        "cropUrl": f"{IMAGE_ROOT}/{row['crop_url']}" if row["crop_url"] else None,
        "score": row["score"],
    }


def _layer(runtime, layer):
    path = layer["image_url"]
    if path and not (runtime.image_dir / path).is_file():
        path = ""
    return {
        "layerId": layer["layer_id"],
        "layerName": layer.get("layer_name") or "",
        "imageUrl": f"{IMAGE_ROOT}/{path}" if path else None,
        "entities": [_entity(row) for row in layer["entities"]],
    }


def _observation(runtime, observation):
    if observation is None:
        return None
    return {
        "id": observation["id"],
        "status": observation["status"],
        "voidReason": observation["void_reason"] or None,
        "at": observation["finished_at"] or observation["started_at"],
        "layers": [_layer(runtime, layer) for layer in observation["layers"]],
    }


def _checkpoint(runtime, checkpoint):
    if checkpoint is None:
        return None
    return {
        "id": checkpoint["id"],
        "basedOnObservation": checkpoint["based_on_observation"],
        "at": checkpoint["created_at"],
        "layers": [_stored_layer(runtime, layer) for layer in checkpoint["submitted"]],
        "note": checkpoint["note"],
    }


def _stored_layer(runtime, layer):
    path = layer.get("imageUrl") or ""
    if path.startswith(f"{IMAGE_ROOT}/"):
        path = path[len(IMAGE_ROOT) + 1 :]
    if path and not (runtime.image_dir / path).is_file():
        path = ""
    return {
        "layerId": layer["layerId"],
        "imageUrl": f"{IMAGE_ROOT}/{path}" if path else None,
        "entities": [_stored_entity(runtime, entity) for entity in layer["entities"]],
    }


def _stored_entity(runtime, entity):
    crop = entity.get("cropUrl") or ""
    if crop.startswith(f"{IMAGE_ROOT}/"):
        crop = crop[len(IMAGE_ROOT) + 1 :]
    if crop and not (runtime.image_dir / crop).is_file():
        crop = ""
    return {**entity, "cropUrl": f"{IMAGE_ROOT}/{crop}" if crop else None}


def _change(change):
    return {
        "id": change["id"],
        "observationId": change["observation_id"],
        "at": change["at"],
        "items": change["items"],
    }


def _category(category):
    return {
        "id": category["id"],
        "name": category["name"],
        "shape": category["shape"] or "bordeaux",
        "details": category["details"],
        "refs": [
            {"id": str(ref["id"]), "url": f"{REFERENCE_ROOT}/{ref['path']}"}
            for ref in category["refs"]
        ],
    }


def _sequence(runtime):
    """界面用它判断数据有没有变；观察、核对状态、变化记录共用一个序号。"""
    return sequence.current(runtime.db.conn)


def build_router(runtime):
    router = APIRouter()

    @router.get("/api/snapshot", response_model=Snapshot)
    def snapshot(recent: int = Query(default=20, ge=0, le=200)):
        return Snapshot.model_validate(
            {
                "sequence": _sequence(runtime),
                "layers": [{"id": layer.id, "name": layer.name} for layer in runtime.layers],
                "observations": [
                    _observation(runtime, item) for item in repo.list_observations(runtime.db, limit=recent)
                ],
                "checkpoints": [
                    _checkpoint(runtime, item)
                    for item in checkpoints.list_checkpoints(runtime.db, limit=recent)
                ],
                "categories": [_category(item) for item in repo.list_categories(runtime.db)],
                "categoryAliases": repo.category_aliases(runtime.db),
                "changes": [_change(item) for item in checkpoints.list_changes(runtime.db, limit=recent)],
                "live": runtime.live_status(),
            }
        )

    @router.get("/api/history", response_model=HistoryPage)
    def history(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        observations = [
            _observation(runtime, item)
            for item in repo.list_observations(runtime.db, before_id=before, limit=limit)
        ]
        checkpoint_items = [
            _checkpoint(runtime, item)
            for item in checkpoints.list_checkpoints(runtime.db, before_id=before, limit=limit)
        ]
        changes = [
            _change(item)
            for item in checkpoints.list_changes(runtime.db, before_id=before, limit=limit)
        ]

        merged = [(item["id"], "observation") for item in observations]
        merged += [(item["id"], "checkpoint") for item in checkpoint_items]
        merged += [(item["id"], "change") for item in changes]
        merged.sort(reverse=True)
        page = merged[:limit]
        keep = {item[0] for item in page}

        return HistoryPage(
            observations=[item for item in observations if item["id"] in keep],
            checkpoints=[item for item in checkpoint_items if item["id"] in keep],
            changes=[item for item in changes if item["id"] in keep],
            next_before_id=page[-1][0] if len(page) == limit else None,
        )

    @router.delete("/api/history", status_code=204)
    def delete_history(body: HistoryDelete):
        observation_ids, checkpoint_ids, unknown = repo.classify_history_ids(
            runtime.db, body.ids
        )
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"这些记录不能单独删（事件随观察一起删）：{', '.join(str(i) for i in unknown)}",
            )

        repo.delete_observations(runtime.db, observation_ids)
        checkpoints.delete_checkpoints(runtime.db, checkpoint_ids)
        for observation_id in observation_ids:
            shutil.rmtree(runtime.image_dir / str(observation_id), ignore_errors=True)

    @router.get("/api/observations")
    def observations(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        items = repo.list_observations(runtime.db, before_id=before, limit=limit)
        return {
            "items": [_observation(runtime, item) for item in items],
            "next_before_id": items[-1]["id"] if len(items) == limit else None,
        }

    @router.get("/api/changes")
    def changes(before: int | None = None, limit: int = Query(default=20, ge=1, le=200)):
        items = checkpoints.list_changes(runtime.db, before_id=before, limit=limit)
        return {"items": [_change(item) for item in items], "next_before_id": items[-1]["id"] if len(items) == limit else None}

    @router.post("/api/observations", status_code=202)
    def trigger():
        if not scheduler.manual_allowed(runtime):
            raise HTTPException(status_code=403, detail="人工触发已关闭")
        if not runtime.start_observe("manual"):
            raise HTTPException(status_code=409, detail="已经在观察中")
        return {"accepted": True}

    @router.post("/api/checkpoints")
    def create_checkpoint(body: CommitRequest):
        try:
            checkpoint_id = commit_pipeline.commit(runtime, body)
        except commit_pipeline.ConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "期间来过新的观察",
                    "latestObservationId": exc.latest_observation_id,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _checkpoint(runtime, _latest(runtime, checkpoint_id))

    @router.get("/api/observations/{observation_id}/groups")
    def observation_groups(observation_id: int):
        observation = repo.load_observation(runtime.db, observation_id)
        if observation is None:
            raise HTTPException(status_code=404, detail="没有这次观察")
        threshold = float(runtime.config.get("judge.cluster") or 0.8)
        return {"groups": grouping.unknown_groups(runtime, observation, threshold)}

    @router.get("/api/positions/{layer_id}/{index}")
    def position(layer_id: str, index: int):
        return {
            "layerId": layer_id,
            "index": index,
            "entries": checkpoints.position_history(runtime.db, layer_id, index),
        }

    @router.get("/api/fields", response_model=list[FieldDef])
    def fields(category_id: int | None = None):
        return [FieldDef.model_validate(item) for item in field_store.list_fields(runtime.db, category_id)]

    @router.post("/api/fields", response_model=FieldDef)
    def create_field(body: FieldInput):
        if body.categoryId is not None:
            _category_or_404(runtime, body.categoryId)
        try:
            field_id = field_store.create_field(
                runtime.db, body.key, body.label, body.askable, body.categoryId
            )
        except field_store.KeyTaken as exc:
            raise HTTPException(status_code=409, detail="这一级已经有同名字段") from exc
        return FieldDef.model_validate(field_store.get_field(runtime.db, field_id))

    @router.put("/api/fields/{field_id}", response_model=FieldDef)
    def update_field(field_id: int, body: FieldUpdate):
        if field_store.get_field(runtime.db, field_id) is None:
            raise HTTPException(status_code=404, detail="没有这个字段")
        field_store.update_field(runtime.db, field_id, body.label, body.askable)
        return FieldDef.model_validate(field_store.get_field(runtime.db, field_id))

    @router.delete("/api/fields/{field_id}", status_code=204)
    def delete_field(field_id: int):
        if field_store.get_field(runtime.db, field_id) is None:
            raise HTTPException(status_code=404, detail="没有这个字段")
        field_store.delete_field(runtime.db, field_id)

    @router.put("/api/light")
    def set_light(body: LightRequest):
        try:
            return {"light": runtime.set_light(body.on)}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"驱动柜内灯失败: {exc}") from exc

    @router.get("/api/ai/status")
    def ai_status():
        return runtime.recognition_status()

    @router.get("/api/logs")
    def runtime_logs(limit: int = Query(default=120, ge=1, le=400)):
        return {"items": logs.recent(limit)}

    @router.post("/api/categories/suggest", response_model=SuggestResponse)
    def suggest_for_category(body: SuggestRequest):
        """AI 补全：只出建议，不写库。"""
        return runtime.suggest_category(body.category_id, body.name, body.details, body.crop_url)

    @router.post("/api/categories")
    def create_category(body: CategoryInput):
        try:
            category_id = repo.create_category(runtime.db, body.name, body.shape, body.details)
        except repo.NameTaken as exc:
            raise HTTPException(status_code=409, detail="已有同名类别") from exc
        runtime.reload_references()
        return _category(_category_or_404(runtime, category_id))

    @router.put("/api/categories/{category_id}")
    def update_category(category_id: int, body: CategoryUpdate):
        _category_or_404(runtime, category_id)
        try:
            repo.update_category(runtime.db, category_id, body.name, body.shape, body.details)
        except repo.NameTaken as exc:
            raise HTTPException(status_code=409, detail="已有同名类别") from exc
        runtime.reload_references()
        return _category(_category_or_404(runtime, category_id))

    @router.get("/api/categories/{category_id}/usage")
    def category_usage(category_id: int):
        _category_or_404(runtime, category_id)
        return {"entities": repo.category_usage(runtime.db, category_id)}

    @router.delete("/api/categories/{category_id}", status_code=204)
    def delete_category(category_id: int):
        _category_or_404(runtime, category_id)
        for path in repo.delete_category(runtime.db, category_id):
            _drop_reference_file(runtime, path)
        runtime.reload_references()

    @router.post("/api/categories/{category_id}/merge", status_code=204)
    def merge_category(category_id: int, body: MergeRequest):
        if category_id == body.target_id:
            raise HTTPException(status_code=400, detail="不能合并到自己")
        _category_or_404(runtime, category_id)
        _category_or_404(runtime, body.target_id)
        repo.merge_categories(runtime.db, category_id, body.target_id)
        runtime.reload_references()

    @router.delete("/api/references/{reference_id}", status_code=204)
    def delete_reference(reference_id: int):
        path = repo.delete_reference(runtime.db, reference_id)
        if path is None:
            raise HTTPException(status_code=404, detail="没有这张参考图")
        _drop_reference_file(runtime, path)
        runtime.reload_references()

    @router.get("/api/cameras", response_model=CameraListOut)
    def cameras():
        return {"cameras": list_cameras()}

    @router.get("/api/layers", response_model=LayerListOut)
    def layers():
        return {
            "configured": [
                {"id": layer.id, "name": layer.name} for layer in runtime.layers
            ],
            "used": repo.used_layers(runtime.db),
        }

    @router.get("/api/settings", response_model=SettingsState)
    def settings():
        return SettingsState.model_validate(runtime.config.state())

    @router.post("/api/settings", response_model=SaveSettingsResult)
    def save_settings(body: SaveSettingsRequest):
        try:
            changed, need_service, need_device = runtime.apply_settings(
                body.group, body.values, body.passcode
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SaveSettingsResult(
            changed=changed, need_service=need_service, need_device=need_device
        )

    @router.get("/api/network", response_model=NetworkState)
    def network_state():
        return NetworkState(**network.current())

    @router.post("/api/network", response_model=NetworkState)
    def network_switch(body: NetworkSwitch):
        if not runtime.config.check_passcode(body.passcode):
            detail = "口令不对" if runtime.config.get("sec.passcode") else "还没设设备口令"
            raise HTTPException(status_code=403, detail=detail)
        try:
            network.connect(body.ssid, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return NetworkState(**network.current())

    @router.post("/api/maintenance")
    def run_maintenance(body: MaintenanceRequest):
        if body.action != "export" and not runtime.config.check_passcode(body.passcode):
            detail = "口令不对" if runtime.config.get("sec.passcode") else "还没设设备口令，先设口令再重启"
            raise HTTPException(status_code=403, detail=detail)
        try:
            message = maintenance.run(runtime, body.action)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"message": message}

    @router.get("/api/events")
    async def events():
        queue = runtime.bus.subscribe(asyncio.get_running_loop())

        async def stream():
            try:
                while True:
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    payload = json.dumps(data, ensure_ascii=False)
                    yield f"event: {event}\ndata: {payload}\n\n"
            finally:
                runtime.bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get(f"{IMAGE_ROOT}/{{path:path}}")
    def image(path: str):
        return _file(runtime.image_dir, path)

    @router.get(f"{REFERENCE_ROOT}/{{path:path}}")
    def reference(path: str):
        return _file(runtime.reference_dir, path)

    return router


def _latest(runtime, checkpoint_id):
    for item in checkpoints.list_checkpoints(runtime.db, limit=200):
        if item["id"] == checkpoint_id:
            return item
    return None


def _category_or_404(runtime, category_id):
    for item in repo.list_categories(runtime.db):
        if item["id"] == category_id:
            return item
    raise HTTPException(status_code=404, detail="没有这个类别")


def _drop_reference_file(runtime, path):
    base = runtime.reference_dir.resolve()
    target = (base / path).resolve()
    if str(target).startswith(str(base)) and target.is_file():
        target.unlink()


def _file(root, path):
    base = root.resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(status_code=404, detail="没有这张图")
    return FileResponse(target)
