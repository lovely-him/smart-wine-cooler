"""写操作的请求形状。路径按 REST 组织，动作不进路径。

只放「进得来」的形状；「出得去」的形状在 `server/kernel/objects.py`——
那份是接口契约的唯一手写真相源，前端类型由它生成。
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from server.kernel.objects import BottleShape, Box, MaintenanceAction


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CommitEntity(Model):
    """人确认过的一件。id 为空表示人工新增，后端会补一个。"""

    id: str | None = None
    box: Box
    category_id: int | None = None


class CommitLayer(Model):
    layer_id: str
    entities: list[CommitEntity] = []


class CommitRequest(Model):
    """一次提交是**整柜**的，不能只提交某一层。

    force 只给「已被告知有过新观察、仍选择保留自己的编辑」用；
    不带它时，起点比最新成立的观察旧就会被拒。
    """

    base_observation_id: int | None = None
    layers: list[CommitLayer]
    note: str = ""
    force: bool = False


class CategoryInput(Model):
    name: str = Field(min_length=1)
    shape: BottleShape = "bordeaux"
    details: dict[str, str] = {}


class CategoryUpdate(Model):
    """改名、换瓶型、改详情。参考图不在这里改——它只由提交产生。"""

    name: str = Field(min_length=1)
    shape: BottleShape = "bordeaux"
    details: dict[str, str] = {}


class MergeRequest(Model):
    """把路径上的那个类别并到这里给的目标类别。"""

    target_id: int


class LightRequest(Model):
    on: bool


class NetworkSwitch(Model):
    """换到另一个局域网。改完设备的地址会变，当前页面通常就断了。"""

    ssid: str = Field(min_length=1)
    password: str = ""
    passcode: str = ""


class SuggestRequest(Model):
    """问大模型补全类别表单。

    `details` 是表单当下已填的内容——它是检索锚点，AI 只补空着的字段，不会改这里面的值。
    `crop_url` 是从柜内带进来的那一件（种子图）。
    用「深入查资料」还是默认档由设置页的开关决定，不在这里传。
    """

    category_id: int | None = None
    name: str = ""
    details: dict[str, str] = {}
    crop_url: str | None = None


class FieldInput(Model):
    """新增一个字段。key 建好之后不让改——改 key 等于换字段，值就对不上了。"""

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    askable: bool = True
    categoryId: int | None = None


class FieldUpdate(Model):
    label: str = Field(min_length=1)
    askable: bool = True


class HistoryDelete(Model):
    """要硬删的历史记录 id。观察与核对状态共用一个序号，所以一份 id 就够。"""

    ids: list[int] = Field(min_length=1)


class SaveSettingsRequest(Model):
    group: str
    values: dict[str, str | int | float | bool]
    passcode: str = ""


class MaintenanceRequest(Model):
    action: MaintenanceAction
    passcode: str = ""


__all__ = [
    "CategoryInput",
    "CategoryUpdate",
    "CommitEntity",
    "CommitLayer",
    "CommitRequest",
    "FieldInput",
    "FieldUpdate",
    "HistoryDelete",
    "LightRequest",
    "MaintenanceRequest",
    "MergeRequest",
    "NetworkSwitch",
    "SaveSettingsRequest",
    "SuggestRequest",
]
