"""把 service 抛出的业务错误映射成 HTTP 状态码。

映射只写一次：service 不认识 HTTP，接口层不判断业务。
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.kernel.errors import ServiceError


def install_error_handlers(app: FastAPI):
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
