# 柜内盘点相机

**当前阶段：方案可行性验证。** 算法与交互在宿主机上验证（图片即可跑），落板能力在 RK3588 上单独验证
（采集、NPU 推理、时延）；功能与网页在宿主完成后统一部署到 RK3588。

目标平台与运行方式的约定见 [`docs/engineering.md`](docs/engineering.md)。

- 产品方案：[`docs/product.md`](docs/product.md)
- 盘点与确认闭环：[`docs/inventory-loop.md`](docs/inventory-loop.md)
- 工程结构与运行环境：[`docs/engineering.md`](docs/engineering.md)
- 未决事项：[`docs/open-questions.md`](docs/open-questions.md)
- 实验结论：[`docs/experiments/`](docs/experiments/)（检测计数 / 外观分类 / 采集通道）
- 变更记录：[`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- 约束清单：[`CLAUDE.md`](CLAUDE.md)

## 目录

| 路径 | 用途 |
| --- | --- |
| `docs/` | 产品方案、盘点闭环、工程约定、未决项、实验结论、变更记录 |
| `tools/embed_lab/` | 目前唯一可执行代码：检测、裁剪、特征评估 |
| `tools/` | 开发期工具；依赖声明与共用 venv 都在这一层 |
| `cache/` | 模型、wheel、运行时库、黄金图的本地缓存（不入库） |
| `tmp/` | 临时目录、外部参考仓库、样本图（不入库） |

## 快速开始

```sh
./tools/embed_lab/run.sh setup                      # 建 venv + 装依赖 + 下模型（只需一次）
./tools/embed_lab/run.sh check                      # 自检环境、模型、数据

./tools/embed_lab/run.sh detect tmp/20261117134147  # 检测瓶子、报数量与位置、裁出单瓶
./tools/embed_lab/run.sh eval                       # 参考库 → 查询集的检索效果
```

`detect` 会对图片做绕光轴旋转（默认 90°）再检测 —— 这不是可选项：横躺瓶子在通用检测器上
`bottle` 类完全不响应，详见 `docs/experiments/detection-lab.md`。

## 数据布局

图片按**用途**分第一层目录，类别/来源/场景等语义写在 `manifest.jsonl` 里：

```
tools/embed_lab/data/
├── manifest.jsonl   {"path","split","label","source","scene","note"}
├── ref/             参考图（原型库）
├── query/           查询图（现场实测）
├── neg/             负样本
└── crop/            检测裁剪产物
```

数据与模型都不入库；`run.sh` 会按需创建目录。

## 进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | 宿主侧可行性：检测/计数/裁剪、外观特征可分性、阈值方向 | 进行中（检测已验证，分类样本不足） |
| P1 | 闭环机制：触发、观测、差异、确认、基线版本 | 未开始 |
| P2 | 建档流程：冷启动建档、合并、归档、参考图积累 | 未开始 |
| P3 | 设备端：采集、推理、网页在同一台设备上跑通 | 未开始（RK3588 上采集与 CPU 推理基准已完成） |
| P4 | 交付化：硬件触发接入、断网自愈、幂等部署 | 未开始 |
