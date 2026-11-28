"""事件总线：广播给所有订阅者，关闭时让长连接自己结束。

SSE 是长连接，不收掉的话 uvicorn 退出会一直等它——这条钉住「关停时能收掉」。
"""

import asyncio

from server.platform.events import EventBus


def _loop():
    return asyncio.get_running_loop()


def test_publish_reaches_subscribers():
    async def scenario():
        bus = EventBus()
        queue = bus.subscribe(_loop())
        bus.publish("observe.done", {"status": "done"})
        await asyncio.sleep(0)
        assert await asyncio.wait_for(queue.get(), timeout=1) == (
            "observe.done",
            {"status": "done"},
        )

    asyncio.run(scenario())


def test_shutdown_wakes_waiting_streams():
    async def scenario():
        bus = EventBus()
        queue = bus.subscribe(_loop())
        bus.shutdown()
        await asyncio.sleep(0)
        assert await asyncio.wait_for(queue.get(), timeout=1) is None

    asyncio.run(scenario())


def test_subscribe_after_shutdown_closes_immediately():
    async def scenario():
        bus = EventBus()
        bus.subscribe(_loop())
        bus.shutdown()
        later = bus.subscribe(_loop())
        assert await asyncio.wait_for(later.get(), timeout=1) is None

    asyncio.run(scenario())


def test_unsubscribe_stops_delivery():
    async def scenario():
        bus = EventBus()
        queue = bus.subscribe(_loop())
        bus.unsubscribe(queue)
        bus.publish("observe.done", {"status": "done"})
        await asyncio.sleep(0)
        assert queue.empty()

    asyncio.run(scenario())
