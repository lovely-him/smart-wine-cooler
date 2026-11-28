"""配置、配网、维护动作、运行日志、设备与柜层列表。

危险动作（改网络、重启）都要设备口令，判口令这件事只在这里做一次。
"""

from server.platform import logs
from server.platform import network
from server.vision.devices import list_cameras

from server.kernel.errors import BadRequest, Forbidden, ServerError


class SettingsService:
    def __init__(self, config, apply_settings, layers, used_layers, run_maintenance):
        self._config = config
        self._apply_settings = apply_settings
        self._layers = layers
        self._used_layers = used_layers
        self._run_maintenance = run_maintenance

    def state(self):
        return self._config.state()

    def save(self, group, values, passcode):
        try:
            changed, need_service, need_device = self._apply_settings(group, values, passcode)
        except PermissionError as exc:
            raise Forbidden(str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise BadRequest(str(exc)) from exc
        return {"changed": changed, "need_service": need_service, "need_device": need_device}

    def network(self):
        return network.current()

    def switch_network(self, ssid, password, passcode):
        if not self._config.check_passcode(passcode):
            raise Forbidden(self._passcode_detail("口令不对", "还没设设备口令"))
        try:
            network.connect(ssid, password)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return network.current()

    def maintenance(self, action, passcode):
        if action != "export" and not self._config.check_passcode(passcode):
            raise Forbidden(self._passcode_detail("口令不对", "还没设设备口令，先设口令再重启"))
        try:
            return {"message": self._run_maintenance(action)}
        except PermissionError as exc:
            raise Forbidden(str(exc)) from exc
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        except RuntimeError as exc:
            raise ServerError(str(exc)) from exc

    def runtime_logs(self, limit=None):
        return {"items": logs.recent(limit)}

    def cameras(self):
        return {"cameras": list_cameras()}

    def layers(self):
        return {
            "configured": [{"id": layer.id, "name": layer.name} for layer in self._layers()],
            "used": self._used_layers(),
        }

    def _passcode_detail(self, wrong, unset):
        return wrong if self._config.get("sec.passcode") else unset
