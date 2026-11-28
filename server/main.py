"""服务入口。

启动参数走「命令行 > 环境变量 > 约定位置」：监听地址与端口也属于引导信息，
不进数据库——数据库一坏就永远起不来。
"""

import argparse
import logging
import os

import uvicorn

from server.platform import logs
from server.app import create_app

# 退出时给在飞的请求留多少秒；超时 uvicorn 会取消剩下的任务
GRACEFUL_SECONDS = 5


class Server(uvicorn.Server):
    """收到 SIGTERM/SIGINT 时先收掉 SSE 流，再走 uvicorn 的退出流程。

    uvicorn 退出时会等连接自然关闭，而 SSE 是长连接：不收掉就只能等 systemd
    的超时把它 SIGKILL 掉（默认 90 秒）。
    """

    def __init__(self, config, drain):
        super().__init__(config)
        self._drain = drain

    def handle_exit(self, sig, frame):
        self._drain()
        super().handle_exit(sig, frame)


def resolve_bind(argv=None):
    parser = argparse.ArgumentParser(description="wine-cooler 服务")
    parser.add_argument("--host", default=os.environ.get("SERVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SERVER_PORT", "8080")))
    args = parser.parse_args(argv)
    return args.host, args.port


def main(argv=None):
    host, port = resolve_bind(argv)

    # 必须绕开 uvicorn 自带的日志配置：rknnlite 的 C 扩展会把 logging._nameToLevel
    # 整体换成单字母（C/D/E/I/W），dictConfig 再按名字查 'INFO' 就直接抛异常，
    # 表现为服务一启动就退出。自己用整数级别配好，uvicorn 只负责跑。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logs.install()
    # ws="none" 同时避开系统旧版 websockets：uvicorn 默认会导入它，而老版本里
    # 没有 ServerProtocol，导入即失败。本服务只用 HTTP。
    app = create_app()
    Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,
            ws="none",
            timeout_graceful_shutdown=GRACEFUL_SECONDS,
        ),
        drain=lambda: app.state.bus.shutdown(),
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
