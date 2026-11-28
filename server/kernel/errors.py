"""业务错误：service 只说明「出了什么事」，HTTP 状态码由 api 层映射。

这样 service 不依赖 FastAPI，换个入口（命令行、定时任务）也能复用同一套判断。
"""


class ServiceError(Exception):
    status = 400

    def __init__(self, detail):
        super().__init__(detail if isinstance(detail, str) else "请求无法完成")
        self.detail = detail


class BadRequest(ServiceError):
    status = 400


class Forbidden(ServiceError):
    status = 403


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class ServerError(ServiceError):
    status = 500
