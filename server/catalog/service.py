"""类别、详情字段、参考图，以及 AI 补全。

写操作改的就是「事实」本身，所以每次改完都要刷新分类器手里的参考库——
漏掉这一句，界面改完要等下次重启才生效。
"""

from server.platform.db import categories, fields as field_store, references
from server.platform.db import categories, references

from server.kernel.errors import BadRequest, Conflict, NotFound
from server.platform.views import as_category, rel


class CatalogService:
    def __init__(self, db, reference_dir, reload_references, suggest):
        self._db = db
        self._reference_dir = reference_dir
        self._reload_references = reload_references
        self._suggest = suggest

    def _category(self, category_id):
        for item in categories.list_categories(self._db):
            if item["id"] == category_id:
                return item
        raise NotFound("没有这个类别")

    def create_category(self, name, shape, details):
        try:
            category_id = categories.create_category(self._db, name, shape, details)
        except categories.NameTaken as exc:
            raise Conflict("已有同名类别") from exc
        self._reload_references()
        return as_category(self._category(category_id))

    def update_category(self, category_id, name, shape, details):
        self._category(category_id)
        try:
            categories.update_category(self._db, category_id, name, shape, details)
        except categories.NameTaken as exc:
            raise Conflict("已有同名类别") from exc
        self._reload_references()
        return as_category(self._category(category_id))

    def category_usage(self, category_id):
        self._category(category_id)
        return {"entities": categories.category_usage(self._db, category_id)}

    def delete_category(self, category_id):
        self._category(category_id)
        for path in categories.delete_category(self._db, category_id):
            self._drop_reference_file(path)
        self._reload_references()

    def merge_category(self, category_id, target_id):
        if category_id == target_id:
            raise BadRequest("不能合并到自己")
        self._category(category_id)
        self._category(target_id)
        categories.merge_categories(self._db, category_id, target_id)
        self._reload_references()

    def fields(self, category_id=None):
        return field_store.list_fields(self._db, category_id)

    def create_field(self, key, label, askable, category_id):
        if category_id is not None:
            self._category(category_id)
        try:
            return field_store.get_field(
                self._db,
                field_store.create_field(self._db, key, label, askable, category_id),
            )
        except field_store.KeyTaken as exc:
            raise Conflict("这一级已经有同名字段") from exc

    def update_field(self, field_id, label, askable):
        if field_store.get_field(self._db, field_id) is None:
            raise NotFound("没有这个字段")
        field_store.update_field(self._db, field_id, label, askable)
        return field_store.get_field(self._db, field_id)

    def delete_field(self, field_id):
        if field_store.get_field(self._db, field_id) is None:
            raise NotFound("没有这个字段")
        field_store.delete_field(self._db, field_id)

    def delete_reference(self, reference_id):
        path = references.delete_reference(self._db, reference_id)
        if path is None:
            raise NotFound("没有这张参考图")
        self._drop_reference_file(path)
        self._reload_references()

    def suggest(self, category_id, name, details, crop_url):
        return self._suggest(category_id, name, details, rel(crop_url))

    def _drop_reference_file(self, path):
        base = self._reference_dir.resolve()
        target = (base / path).resolve()
        if str(target).startswith(str(base)) and target.is_file():
            target.unlink()
