"""接口数据模型。这是**唯一的手写真相源**——前端类型由它的 OpenAPI 生成。

形状与前端的 domain 层一一对应：那边已经实现了「当前状态四来源优先级」和差异计算，
后端重复一套只会让两边各说各话。JSON 用驼峰，Python 侧仍是下划线。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

BottleShape = Literal["bordeaux", "burgundy", "slim", "champagne", "whisky"]
DoorState = Literal["open", "closed"]
MaintenanceAction = Literal["restart-service", "restart-device", "export"]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Box(Model):
    x: float
    y: float
    w: float
    h: float


class Entity(Model):
    id: str
    box: Box
    category_id: int | None = None
    crop_url: str | None = None
    score: float | None = None


class LayerRecord(Model):
    layer_id: str
    layer_name: str = ""
    image_url: str | None = None
    entities: list[Entity] = []


class Observation(Model):
    id: int
    status: Literal["done", "void"]
    void_reason: str | None = None
    at: datetime | None = None
    layers: list[LayerRecord] = []


class CategoryRef(Model):
    id: str
    url: str


class Category(Model):
    id: int
    name: str
    shape: BottleShape = "bordeaux"
    details: dict[str, str] = {}
    refs: list[CategoryRef] = []


class StoredEntity(Model):
    id: str
    box: Box
    category_id: int | None = None
    crop_url: str | None = None


class CheckpointLayer(Model):
    layer_id: str
    image_url: str | None = None
    entities: list[StoredEntity] = []


class Checkpoint(Model):
    id: int
    based_on_observation: int | None = None
    at: datetime | None = None
    layers: list[CheckpointLayer] = []
    note: str = ""


class ChangeItem(Model):
    layer_id: str
    category_id: int | None = None
    delta: int


class ChangeRecord(Model):
    id: int
    observation_id: int
    at: str | None = None
    items: list[ChangeItem] = []


class CameraStatus(Model):
    id: str
    available: bool
    layers: list[str] = []


class LiveStatus(Model):
    """此刻的状况：实时探测，不落盘。"""

    cameras: list[CameraStatus] = []
    storage_free_bytes: int | None = None
    storage_total_bytes: int | None = None
    observing: bool = False
    scan_started_at: datetime | None = None
    last_observation_id: int | None = None
    now: datetime | None = None
    ip: str | None = None
    storage_low: bool = False
    uptime_seconds: int | None = None
    door: DoorState | None = None
    door_level: Literal["low", "high"] | None = None
    light: Literal["on", "off", "unknown"] = "unknown"


class LayerInfo(Model):
    id: str
    name: str


class Snapshot(Model):
    sequence: int
    layers: list[LayerInfo] = []
    observations: list[Observation] = []
    checkpoints: list[Checkpoint] = []
    categories: list[Category] = []
    category_aliases: dict[str, int] = {}
    changes: list[ChangeRecord] = []
    live: LiveStatus


class ObservationPage(Model):
    items: list[Observation] = []
    next_before_id: int | None = None


class HistoryPage(Model):
    """历史时间线上的一页：三类记录合起来按序号倒排取前 N 条。"""

    observations: list[Observation] = []
    checkpoints: list[Checkpoint] = []
    changes: list[ChangeRecord] = []
    next_before_id: int | None = None


class ChangePage(Model):
    items: list[ChangeRecord] = []
    next_before_id: int | None = None
