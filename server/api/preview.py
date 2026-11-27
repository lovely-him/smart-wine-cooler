"""摄像头预览：抓一帧，按草稿参数矫正后返回 JPEG。

抓帧比矫正贵得多（开流一次 0.3 秒以上），所以原图按摄像头缓存：
调参数时只重算矫正，不重新开流。换分辨率或主动刷新时才重新抓。
"""

import io
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from server.hardware import calibrate, lens
from server.hardware.camera import V4l2Camera

CACHE_SECONDS = 60.0
JPEG_QUALITY = 88
WARMUP_SKIP = 12
LIGHT_SETTLE = 0.4
CANVAS_QUALITY = 82


class PreviewRequest(BaseModel):
    id: str
    width: int = 1280
    height: int = 720
    fov: float = 0
    k1: float = 0
    k2: float = 0
    k3: float = 0
    k4: float = 0
    pan_x: float = Field(0, alias="panX")
    pan_y: float = Field(0, alias="panY")
    zoom: float = 4
    rotate: int = 0
    mode: str = "crop"
    refresh: bool = False

    model_config = {"populate_by_name": True}


class CalibrateRequest(BaseModel):
    """`lines` 里的坐标是「整体范围」图上的像素，左上角为原点。"""

    width: int = 1280
    height: int = 720
    rotate: int = 0
    fov: float = 0
    k1: float = 0
    lines: list[list[list[float]]] = []


_lock = threading.Lock()
_frames = {}


def _frame(request, runtime):
    """按摄像头缓存原图；调参数时复用同一张，避免每次重新开流。"""
    with _lock:
        item = _frames.get(request.id)
        size = (request.width, request.height)
        if (
            item is not None
            and not request.refresh
            and item["size"] == size
            and time.monotonic() - item["at"] < CACHE_SECONDS
        ):
            return item["frame"]
        camera = V4l2Camera(
            {
                "device": request.id,
                "width": request.width,
                "height": request.height,
                "skip": WARMUP_SKIP,
                "batch": 1,
            }
        )
        runtime.set_light(True)
        time.sleep(LIGHT_SETTLE)
        try:
            frame = camera.grab(1)[0]
        finally:
            runtime.sync_light()
        _frames[request.id] = {"frame": frame, "size": size, "at": time.monotonic()}
        return frame


def _render(request, frame):
    """先转安装方向，再去畸变——和采集流程同一条流水线。"""
    if request.rotate:
        frame = frame.rotate(request.rotate, expand=True)
    if request.mode == "canvas":
        return lens.overview(
            frame, request.fov, request.k1, request.k2, request.k3, request.k4
        )
    return lens.undistort(
        frame,
        request.fov,
        request.k1,
        request.k2,
        request.k3,
        request.k4,
        request.pan_x,
        request.pan_y,
        request.zoom,
    )


def build_preview_router(runtime):
    router = APIRouter()

    @router.post("/api/cameras/preview")
    def preview(request: PreviewRequest):
        try:
            image = _render(request, _frame(request, runtime))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"取不到画面: {exc}") from exc

        buffer = io.BytesIO()
        quality = CANVAS_QUALITY if request.mode == "canvas" else JPEG_QUALITY
        image.save(buffer, "JPEG", quality=quality)
        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/cameras/calibrate")
    def calibrate_lens(request: CalibrateRequest):
        swapped = request.rotate % 180 != 0
        frame_w = request.height if swapped else request.width
        frame_h = request.width if swapped else request.height
        half_w = frame_w * lens.CANVAS_SCALE / 2.0
        half_h = frame_h * lens.CANVAS_SCALE / 2.0

        lines = []
        for line in request.lines:
            lines.append([(float(x) - half_w, float(y) - half_h) for x, y in line])

        try:
            return calibrate.solve(lines, frame_w, frame_h, request.fov, request.k1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
