"""类别详情表单的字段定义，与 `03-功能清单` 的默认模板一致。

`askable` 表示 AI 能不能查到：标 False 的两项不存在于任何外部资料里，
只在使用者自己脑子里，只能由人录。
"""

from dataclasses import dataclass

NAME_KEY = "名称"


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    askable: bool = True


DETAIL_FIELDS: tuple[FieldDef, ...] = (
    FieldDef("brand", "品牌"),
    FieldDef("series", "系列"),
    FieldDef("barcode", "条码"),
    FieldDef("kind", "品类"),
    FieldDef("producer", "生产商"),
    FieldDef("country", "生产国"),
    FieldDef("region", "产区"),
    FieldDef("bottling", "灌装地"),
    FieldDef("vintage", "年份"),
    FieldDef("grape", "原料/品种"),
    FieldDef("abv", "酒精度"),
    FieldDef("volume", "容量"),
    FieldDef("sweetness", "甜度"),
    FieldDef("packaging", "包装形式"),
    FieldDef("drink_window", "适饮期"),
    FieldDef("storage", "储存条件"),
    FieldDef("after_opening", "开瓶后保存"),
    FieldDef("purchase", "采购来源", askable=False),
    FieldDef("note", "备注", askable=False),
)

ASKABLE = tuple(item for item in DETAIL_FIELDS if item.askable)
BY_KEY = {item.key: item for item in DETAIL_FIELDS}
