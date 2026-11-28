"""摄像头可用性：界面状态里每台采集源的 `available`。

开一次流就知道了，失败即不可用——**不报错**，因为柜层没接摄像头是正常状态。
"""

from server.vision import factory


def cameras(plan):
    result = []
    for camera in plan.cameras:
        try:
            factory.load_camera(camera, plan.grab_cfg)
            available = True
        except Exception:
            available = False
        result.append(
            {
                "id": camera.id,
                "label": camera.label,
                "available": available,
                "layers": [
                    layer.id for layer in plan.layers if layer.camera == camera.id
                ],
            }
        )
    return result
