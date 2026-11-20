"""不联网的实现：没有凭据或没开启时用它，整条标注流水线照样跑得通（全部空候选）。"""

from .base import Provider


class NoopProvider(Provider):
    name = "noop"

    def available(self):
        return True

    def describe(self, image):
        return None
