"""类别、详情字段、参考图、AI 补全。"""

from fastapi import APIRouter

from server.api.schemas import (
    CategoryInput,
    CategoryUpdate,
    FieldInput,
    FieldUpdate,
    MergeRequest,
    SuggestRequest,
)
from server.kernel.objects import FieldDef, SuggestResponse
from server.catalog.service import CatalogService


def build(catalog: CatalogService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/categories/suggest", response_model=SuggestResponse)
    def suggest(body: SuggestRequest):
        return catalog.suggest(body.category_id, body.name, body.details, body.crop_url)

    @router.post("/api/categories")
    def create_category(body: CategoryInput):
        return catalog.create_category(body.name, body.shape, body.details)

    @router.put("/api/categories/{category_id}")
    def update_category(category_id: int, body: CategoryUpdate):
        return catalog.update_category(category_id, body.name, body.shape, body.details)

    @router.get("/api/categories/{category_id}/usage")
    def category_usage(category_id: int):
        return catalog.category_usage(category_id)

    @router.delete("/api/categories/{category_id}", status_code=204)
    def delete_category(category_id: int):
        catalog.delete_category(category_id)

    @router.post("/api/categories/{category_id}/merge", status_code=204)
    def merge_category(category_id: int, body: MergeRequest):
        catalog.merge_category(category_id, body.target_id)

    @router.get("/api/fields", response_model=list[FieldDef])
    def fields(category_id: int | None = None):
        return catalog.fields(category_id)

    @router.post("/api/fields", response_model=FieldDef)
    def create_field(body: FieldInput):
        return catalog.create_field(body.key, body.label, body.askable, body.categoryId)

    @router.put("/api/fields/{field_id}", response_model=FieldDef)
    def update_field(field_id: int, body: FieldUpdate):
        return catalog.update_field(field_id, body.label, body.askable)

    @router.delete("/api/fields/{field_id}", status_code=204)
    def delete_field(field_id: int):
        catalog.delete_field(field_id)

    @router.delete("/api/references/{reference_id}", status_code=204)
    def delete_reference(reference_id: int):
        catalog.delete_reference(reference_id)

    return router
