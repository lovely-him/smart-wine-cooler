"""写操作的请求形状。路径按 REST 组织，动作不进路径。"""

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from pydantic import BaseModel

from .objects import BottleShape, Box, MaintenanceAction


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


class FieldDef(Model):
    """详情表单的一个字段。key 是存储用的键，label 是给人看的。

    categoryId 为空表示这是全局模板；有值表示只属于那个类别。
    """

    id: int = 0
    key: str
    label: str
    askable: bool = True
    categoryId: int | None = None


class LightRequest(Model):
    on: bool


class NetworkState(Model):
    """当前网络：只读展示，凭据不在这里（由系统层自己保管）。"""

    supported: bool = False
    connected: bool = False
    ssid: str = ""
    address: str = ""
    hostname: str = ""
    mdns_url: str = ""


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


class SuggestItem(Model):
    """一条建议。basis 标明来源：label = 图上读到，web = 联网查到。"""

    value: str
    basis: str = "web"


class SuggestConflict(Model):
    """模型查到的和表单里填的不一致。只提示，不自动改写。"""

    key: str
    known: str
    found: str
    basis: str = "web"


class SuggestResponse(Model):
    label_text: str = ""
    name: str = ""
    suggestions: dict[str, SuggestItem] = {}
    conflicts: list[SuggestConflict] = []
    uncertain: list[str] = []
    error: str = ""
    used: int = 0
    limit: int = 0


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


class SettingControlOut(Model):
    key: str
    kind: str
    label: str = ""
    value: str | int | float | bool | None = None
    options: list[str] = []


class SettingItemOut(Model):
    id: str
    title: str
    effect: str
    danger: bool = False
    sensitive: bool = False
    controls: list[SettingControlOut] = []


class SettingGroupOut(Model):
    id: str
    title: str
    items: list[SettingItemOut] = []


class SettingChangeOut(Model):
    id: int
    item_id: str
    title: str
    at: str
    note: str


class SettingsState(Model):
    groups: list[SettingGroupOut] = []
    changes: list[SettingChangeOut] = []


class SaveSettingsRequest(Model):
    group: str
    values: dict[str, str | int | float | bool]
    passcode: str = ""


class SaveSettingsResult(Model):
    changed: list[str] = []
    need_service: list[str] = []
    need_device: list[str] = []


class CameraOut(Model):
    path: str
    label: str
    online: bool = True


class CameraListOut(Model):
    cameras: list[CameraOut] = []


class LayerOut(Model):
    id: str
    name: str


class LayerListOut(Model):
    configured: list[LayerOut] = []
    used: list[LayerOut] = []


class MaintenanceRequest(Model):
    action: MaintenanceAction
    passcode: str = ""
