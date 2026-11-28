"""写入一组配置，并把「改完要跟着动的东西」按改动项重新对齐。

危险项先过口令；判定参数改了要重建推理组件——重建期间不允许正在观察。
"""

from server.platform import logs
from server.platform import network


class SettingsApplier:
    def __init__(
        self,
        config,
        rebuild_light,
        reload_recognizer,
        reload_inference,
        is_observing,
    ):
        self._config = config
        self._rebuild_light = rebuild_light
        self._reload_recognizer = reload_recognizer
        self._reload_inference = reload_inference
        self._is_observing = is_observing

    def apply(self, group, values, passcode):
        config = self._config
        config.guard(values, passcode)
        changed, need_service, need_device = config.set_many(group, values)

        if any(key.startswith("trig.") for key in changed):
            self._rebuild_light(self._is_observing())
        if any(key.startswith("ai.") for key in changed):
            self._reload_recognizer()
        if "sys.log_level" in changed:
            logs.set_level(config.get("sys.log_level"))
        if "net.hostname" in changed:
            network.set_hostname(config.get("net.hostname"))
        if any(key.startswith("judge.") for key in changed):
            if self._is_observing():
                raise PermissionError("正在观察，等这次结束再改判定参数")
            self._reload_inference()
        return changed, need_service, need_device
