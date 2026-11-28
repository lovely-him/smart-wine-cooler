"""图片文件：整层画面与参考图。

路径从界面进来，必须先确认它没有跳出根目录再读。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from server.platform.views import IMAGE_ROOT, REFERENCE_ROOT


def _file(root, path):
    base = root.resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(status_code=404, detail="没有这张图")
    return FileResponse(target)


def build(image_dir, reference_dir) -> APIRouter:
    router = APIRouter()

    @router.get(f"{IMAGE_ROOT}/{{path:path}}")
    def image(path: str):
        return _file(image_dir, path)

    @router.get(f"{REFERENCE_ROOT}/{{path:path}}")
    def reference(path: str):
        return _file(reference_dir, path)

    return router
