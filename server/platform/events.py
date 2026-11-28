"""后端 → 前端的事件通道（SSE）。

推的是「该刷新了」，不是数据本身：数据一律由前端从 `GET /api/snapshot` 取，
界面显示的和数据库里的因此永远是同一份，不用把序列化写两遍。

反向（前端 → 后端）一律走普通 HTTP 请求，不在这里开双向口子。
"""

import asyncio


class EventBus:
    """单进程内广播：每个 SSE 连接一条队列，发布时挨个投递。"""

    def __init__(self):
        self._queues = set()
        self._loop = None
        self._closed = False

    def subscribe(self, loop):
        self._loop = loop
        queue = asyncio.Queue(maxsize=16)
        if self._closed:
            queue.put_nowait(None)
            return queue
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._queues.discard(queue)

    def shutdown(self):
        """收掉所有事件流。

        SSE 是长连接，不收掉 uvicorn 的退出会一直等它自然结束。
        """
        self._closed = True
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in list(self._queues):
            loop.call_soon_threadsafe(self._close, queue)

    def _close(self, queue):
        self._queues.discard(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)

    def publish(self, event, data):
        """从任意线程调用。没有订阅者、或事件循环还没起来时直接丢弃。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in list(self._queues):
            loop.call_soon_threadsafe(self._offer, queue, (event, data))

    def _offer(self, queue, item):
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass
