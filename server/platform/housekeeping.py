"""空间与记录的生命周期：空间够不够、旧记录怎么回收。

回收的粒度永远是「一次观察」——图片没有独立身份，删记录就连图一起删。
"""

from server.platform import retention
from server.platform import storage


class Housekeeping:
    def __init__(self, db, image_dir, config):
        self._db = db
        self._image_dir = image_dir
        self._config = config

    def usage(self):
        return storage.usage(self._image_dir)

    def storage_low(self):
        floor = float(self._config.get("data.min_free_mb") or 0) * 1024 * 1024
        return self.usage()["free"] < floor

    def reclaim_space(self):
        return retention.reclaim(self._db, self._image_dir, self.storage_low)

    def trim(self):
        keep = int(self._config.get("data.observe_keep") or 0)
        return retention.trim(self._db, self._image_dir, keep)

    def reclaim_orphans(self):
        return retention.reclaim_orphans(self._db, self._image_dir)
