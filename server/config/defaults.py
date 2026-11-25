"""出厂默认值。`04-配置与设置` 只声明"有这一项"，具体数值全部落在这一份里。

每一项带四个属性：
- `danger`：改错会让设备失联/锁死/再也观察不了，改动要过设备口令
- `sensitive`：内容不能外泄，导出包里不出现，变更记录只记"改过"
- `effect`：生效时机，now | service | device
"""

from dataclasses import dataclass, field
from typing import Any

from . import layers as layer_config

NOW = "now"
SERVICE = "service"
DEVICE = "device"


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    kind: str
    default: Any
    danger: bool = False
    sensitive: bool = False
    effect: str = NOW
    choices: tuple[str, ...] = field(default=())
    label: str = ""


SETTINGS: tuple[SettingDef, ...] = (
    # ── NET 网络 ──
    SettingDef("net.hostname", "net", "text", "wine-cooler", label="名字"),
    # ── CAM 采集 ──
    SettingDef(
        "cam.layers", "cam", "layers", layer_config.default_json(), danger=True, label="层与摄像头"
    ),
    SettingDef("cam.width", "cam", "number", 1280, label="画面宽"),
    SettingDef("cam.height", "cam", "number", 720, label="画面高"),
    SettingDef(
        "cam.rotate",
        "cam",
        "select",
        "90",
        choices=("0", "90", "180", "270"),
        label="画面朝向",
    ),
    SettingDef("cam.batch", "cam", "number", 10, label="一次抓几帧"),
    SettingDef("cam.skip", "cam", "number", 4, label="丢弃开头几帧"),
    # ── TRIG 触发 ──
    SettingDef("trig.door_enabled", "trig", "switch", False, label="启用门磁 / 按键"),
    SettingDef("trig.door_gpio", "trig", "text", "", effect=SERVICE, label="GPIO"),
    SettingDef(
        "trig.door_closed_level",
        "trig",
        "radio",
        "low",
        choices=("low", "high"),
        effect=SERVICE,
        label="关上门时读到",
    ),
    SettingDef("trig.light_gpio", "trig", "text", "", effect=SERVICE, label="GPIO"),
    SettingDef(
        "trig.light_active_level",
        "trig",
        "radio",
        "high",
        choices=("low", "high"),
        effect=SERVICE,
        label="亮灯时有效电平",
    ),
    SettingDef("trig.timer_enabled", "trig", "switch", False, label="启用定时观察"),
    SettingDef("trig.timer_minutes", "trig", "number", 120, label="间隔（分钟）"),
    SettingDef("trig.manual_enabled", "trig", "switch", True, label="允许手工触发"),
    # ── JUDGE 判定 ──
    SettingDef("judge.conf", "judge", "number", 0.3, label="检测置信度下限"),
    SettingDef("judge.tau", "judge", "number", 0.68, label="相似度下限"),
    SettingDef("judge.margin", "judge", "number", 0.02, label="要领先第二名多少"),
    SettingDef("judge.cluster", "judge", "number", 0.8, label="分到一组的阈值"),
    SettingDef("judge.ref_dedupe", "judge", "number", 0.95, label="参考图去重阈值"),
    SettingDef("judge.ref_cap", "judge", "number", 12, label="每类参考图上限"),
    SettingDef("judge.stable_frames", "judge", "number", 3, label="连续合格帧数"),
    SettingDef("judge.diff_thresh", "judge", "number", 1.5, label="帧间差异上限"),
    SettingDef("judge.min_bright", "judge", "number", 15, label="最暗亮度"),
    SettingDef("judge.max_bright", "judge", "number", 245, label="最亮亮度"),
    SettingDef("judge.min_sharp", "judge", "number", 20, label="最低清晰度"),
    SettingDef("judge.max_frames", "judge", "number", 30, label="最多抓几帧"),
    SettingDef("judge.settle_timeout", "judge", "number", 10, label="等静止最长秒数"),
    # ── AI 大模型 ──
    SettingDef("ai.enabled", "ai", "switch", False, label="启用"),
    SettingDef("ai.endpoint", "ai", "text", "", label="服务地址"),
    SettingDef("ai.key", "ai", "password", "", sensitive=True, label="访问密钥"),
    SettingDef("ai.model", "ai", "text", "", label="型号"),
    SettingDef("ai.daily_limit", "ai", "number", 50, label="每天最多调用"),
    SettingDef("ai.max_side", "ai", "number", 1280, label="上传边长上限"),
    SettingDef("ai.deep", "ai", "switch", False, label="深入查资料"),
    # ── DATA 数据 ──
    SettingDef("data.observe_keep", "data", "number", 200, label="观察记录最多留几条"),
    SettingDef("data.export_target", "data", "text", "", label="导出目标路径"),
    SettingDef("data.min_free_mb", "data", "number", 500, label="最低剩余空间 MB"),
    # ── SEC 安全 ──
    SettingDef("sec.passcode", "sec", "password", "", danger=True, sensitive=True, label="设备口令"),
    SettingDef("sec.passcode_hint", "sec", "text", "", label="口令提示"),
    # ── SYS 系统 ──
    SettingDef("sys.log_level", "sys", "select", "info", choices=("info", "warn", "error"), label="日志级别"),
    SettingDef("sys.autostart", "sys", "switch", True, effect=DEVICE, label="开机自动启动"),
)

GROUPS: tuple[tuple[str, str], ...] = (
    ("net", "网络"),
    ("cam", "采集"),
    ("trig", "触发"),
    ("judge", "判定"),
    ("ai", "大模型"),
    ("data", "数据"),
    ("sec", "安全"),
    ("sys", "系统"),
)


@dataclass(frozen=True)
class SettingItemDef:
    """界面上的一条设置。一条可以对应多个底层键（例如分辨率由宽高两个键组成）。"""

    id: str
    group: str
    title: str
    keys: tuple[str, ...]


ITEMS: tuple[SettingItemDef, ...] = (
    SettingItemDef("NET-01", "net", "当前网络", ()),
    SettingItemDef("NET-02", "net", "本机名称", ("net.hostname",)),
    SettingItemDef("CAM-01", "cam", "层与摄像头", ("cam.layers",)),
    SettingItemDef("CAM-02", "cam", "采集分辨率", ("cam.width", "cam.height")),
    SettingItemDef("CAM-03", "cam", "画面朝向", ("cam.rotate",)),
    SettingItemDef("CAM-04", "cam", "抓几帧、丢开头几帧", ("cam.batch", "cam.skip")),
    SettingItemDef("TRIG-01", "trig", "门磁 / 按键触发", ("trig.door_enabled",)),
    SettingItemDef(
        "TRIG-02",
        "trig",
        "门磁",
        ("trig.door_gpio", "trig.door_closed_level"),
    ),
    SettingItemDef(
        "TRIG-03", "trig", "定时观察", ("trig.timer_enabled", "trig.timer_minutes")
    ),
    SettingItemDef("TRIG-04", "trig", "手工触发", ("trig.manual_enabled",)),
    SettingItemDef("TRIG-05", "trig", "柜内灯", ("trig.light_gpio", "trig.light_active_level")),
    SettingItemDef("JUDGE-01", "judge", "多高的置信度算看到一个目标", ("judge.conf",)),
    SettingItemDef("JUDGE-02", "judge", "认出类别所需的相似度与领先幅度", ("judge.tau", "judge.margin")),
    SettingItemDef("JUDGE-03", "judge", "把「看起来一样」分到一组的松紧", ("judge.cluster",)),
    SettingItemDef("JUDGE-04", "judge", "新裁剪图与已有参考图差多少才算新角度", ("judge.ref_dedupe",)),
    SettingItemDef("JUDGE-05", "judge", "每个类别最多留几张参考图", ("judge.ref_cap",)),
    SettingItemDef(
        "JUDGE-06",
        "judge",
        "画面静止与可用性判定",
        (
            "judge.stable_frames",
            "judge.diff_thresh",
            "judge.min_bright",
            "judge.max_bright",
            "judge.min_sharp",
            "judge.max_frames",
            "judge.settle_timeout",
        ),
    ),
    SettingItemDef("AI-01", "ai", "启用外部模型辅助", ("ai.enabled",)),
    SettingItemDef("AI-02", "ai", "服务地址", ("ai.endpoint",)),
    SettingItemDef("AI-03", "ai", "访问密钥", ("ai.key",)),
    SettingItemDef("AI-04", "ai", "使用哪个型号", ("ai.model",)),
    SettingItemDef("AI-05", "ai", "每天最多调用几次", ("ai.daily_limit",)),
    SettingItemDef("AI-06", "ai", "上传画面的边长上限", ("ai.max_side",)),
    SettingItemDef("AI-07", "ai", "补全时是否深入查资料（更慢更贵）", ("ai.deep",)),
    SettingItemDef("DATA-01", "data", "观察记录最多留几条", ("data.observe_keep",)),
    SettingItemDef("DATA-04", "data", "导出的目标地址", ("data.export_target",)),
    SettingItemDef("DATA-05", "data", "剩余空间低于多少就不许观察", ("data.min_free_mb",)),
    SettingItemDef("SEC-01", "sec", "设备口令", ("sec.passcode", "sec.passcode_hint")),
    SettingItemDef("SYS-01", "sys", "日志详细程度", ("sys.log_level",)),
    SettingItemDef("SYS-02", "sys", "开机是否自动启动", ("sys.autostart",)),
)

BY_KEY = {item.key: item for item in SETTINGS}
DEFAULTS = {item.key: item.default for item in SETTINGS}
GROUP_TITLES = dict(GROUPS)
ITEMS_BY_ID = {item.id: item for item in ITEMS}
