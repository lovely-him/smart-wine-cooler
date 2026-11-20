# embed_lab — 方案可行性验证工具

在宿主机上回答两个问题，**不涉及设备端代码**：

| 问题 | 判定 | 详见 |
| --- | --- | --- |
| 能否从照片里定位瓶子、数对数量、裁出单瓶 | 逐图瓶子数与真值对比 | [`docs/experiments/detection-lab.md`](../../docs/experiments/detection-lab.md) |
| 外观特征能否区分品类 | 检索 top-1 与相似度分布 | [`docs/experiments/embedding-lab.md`](../../docs/experiments/embedding-lab.md) |

## 依赖

- Python 3.10+
- `numpy` / `pillow` / `onnxruntime` / `onnx` / `requests`（`setup` 装进本地 `.venv`）
- 模型：特征模型 MobileNetV2、检测模型 YOLOv8n（COCO），`setup` 自动下载

pip 镜像写在 `.venv/pip.conf`（默认清华源），可用环境变量 `PIP_INDEX` 覆盖。

## 用法

```sh
./tools/embed_lab/run.sh setup                        # 建 venv + 装依赖 + 下模型（只需一次）
./tools/embed_lab/run.sh check                        # 自检环境、模型、数据与 manifest

./tools/embed_lab/run.sh detect tmp/20261117134147    # 检测 + 裁剪（默认旋转 90°）
./tools/embed_lab/run.sh eval                         # 检索评估

# 网络图作参考库（Wikimedia Commons，给分类名比给关键词准）
./tools/embed_lab/run.sh fetch-ref coke_500 10 "Coca-Cola bottles"
./tools/embed_lab/run.sh fetch-neg tool 10 "Hand tools"
```

`detect` 的旋转角不是可选项：通用检测器对**横躺**瓶子几乎不响应，绕光轴转到瓶轴竖直才恢复。
只有一种固定机位时填一个角度（如 `90`）；机位会变时传 `--rotate` 对应角度或 `0,90,180,270` 多方向合并。

## 数据布局

第一层目录只按**用途**分层；类别、来源、场景写在 `data/manifest.jsonl`，不用目录名表达。

```
data/
├── manifest.jsonl
├── ref/        参考图（原型库）
├── query/      查询图（现场实测）
├── neg/        负样本
└── crop/       检测裁剪产物
```

manifest 每行一条：

| 字段 | 含义 |
| --- | --- |
| `path` | 相对 `data/` 的路径 |
| `split` | `ref` / `query` / `neg` / `crop` |
| `label` | 类别；`ref` 必填，`query` 可不填（不填就不参与 top-1 统计） |
| `source` | `net`（网络图）/ `field`（现场）/ `detect`（自动裁剪） |
| `scene` | 场景或批次标识，自由文本 |
| `note` | 备注（来源标题、置信度、位置等） |

`detect` 与 `fetch-*` 会自动追加记录；裁剪图的 `label` 留空，需人工补。

## 输出

`out/report.json` 与终端表格：

| 键 | 含义 |
| --- | --- |
| `ref_self` | 参考图内部一致性（留一法），只说明库自身可分，不代表现场效果 |
| `ref_to_query` | **跨域**：库=参考图，查询=有类标的查询图 |
| `query_self` | 查询图内部一致性（留一法） |
| `ref_plus_query_to_query` | 实际形态：库=参考图+查询图，留一法 |
| `ref_to_neg` | 负样本相似度分布，用于参考 `tau` |

阈值建议取 Youden J 最优点，`margin` 取正确分类中间隔的 p05。
**注意**：实测同类相似度下限会低于异类上限，`tau` 只能作下限保护，判定要以检索排序 + margin 为主。

## 已知限制

- 检测模型的 `bottle` 类只对**竖直**姿态可靠；横躺/斜放必须靠旋转补偿
- 特征模型是 ImageNet 预训练，区分的是物体大类，同品类不同包装的细粒度区分能力有限
- 输入按 224×224 直接拉伸（保持长宽比会削掉瓶盖/瓶底）
