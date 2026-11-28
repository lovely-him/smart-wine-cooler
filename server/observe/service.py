"""观察：人工触发、柜内灯、识别进度、零档案分组。

触发这一路不排队：正在观察时再触发只会被拒，理由原样返回给界面。
"""

from server.platform.db import observations

from server.kernel.errors import Conflict, Forbidden, NotFound, ServerError


class ObserveService:
    def __init__(
        self,
        db,
        config,
        start_observe,
        manual_allowed,
        blocked_reason,
        set_light,
        recognition_status,
        unknown_groups,
    ):
        self._db = db
        self._config = config
        self._start_observe = start_observe
        self._manual_allowed = manual_allowed
        self._blocked_reason = blocked_reason
        self._set_light = set_light
        self._recognition_status = recognition_status
        self._unknown_groups = unknown_groups

    def trigger(self):
        if not self._manual_allowed():
            raise Forbidden("人工触发已关闭")
        reason = self._blocked_reason()
        if reason:
            raise Conflict(reason)
        if not self._start_observe("manual"):
            raise Conflict("已经在观察中")
        return {"accepted": True}

    def set_light(self, on):
        try:
            return {"light": self._set_light(on)}
        except RuntimeError as exc:
            raise Conflict(str(exc)) from exc
        except Exception as exc:
            raise ServerError(f"驱动柜内灯失败: {exc}") from exc

    def recognition(self):
        return self._recognition_status()

    def unknown_groups(self, observation_id):
        observation = observations.load_observation(self._db, observation_id)
        if observation is None:
            raise NotFound("没有这次观察")
        threshold = float(self._config.get("judge.cluster") or 0.8)
        return {"groups": self._unknown_groups(observation, threshold)}
