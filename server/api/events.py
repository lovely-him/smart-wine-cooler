"""后端 → 前端的事件流（SSE）。

推的是「该刷新了」，不是数据本身：数据一律由界面回快照取，
界面显示的与库里存的因此永远是同一份。
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

PING_SECONDS = 15


def build(bus) -> APIRouter:
    router = APIRouter()

    @router.get("/api/events")
    async def events():
        queue = bus.subscribe(asyncio.get_running_loop())

        async def stream():
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=PING_SECONDS
                        )
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if item is None:
                        return
                    event, data = item
                    payload = json.dumps(data, ensure_ascii=False)
                    yield f"event: {event}\ndata: {payload}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
