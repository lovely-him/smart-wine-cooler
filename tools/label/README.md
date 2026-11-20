# 批量标注（tools/label）

给一批裁剪图打上商品名，写进参考库。**先本地聚类，再让视觉大模型给每簇一个候选名，最后人工确认**。

## 前提

- 凭据：`export SILICONFLOW_API_KEY=...`（只走环境变量；不进配置、不进日志、不进仓库）
- 开关：`config/app.json` 里 `vision.enabled=true`（默认关闭，设备不主动出网）

## 用法

```bash
tools/label/run.sh scan  --crops <裁剪图目录> [--out tmp/labels.json] [--cluster 0.80] [--no-network]
tools/label/run.sh table --in tmp/labels.json
tools/label/run.sh apply --in tmp/labels.json [--save-notes]
tools/label/run.sh selftest        # 离线自检：不联网、不碰真实库
```

`scan` 之后编辑 `tmp/labels.json`：把 `category` 改成要用的名字，并把 `confirmed` 置为 `true`，再跑 `apply`。

## labels.json

| 字段 | 含义 |
| --- | --- |
| `file` | 裁剪图路径 |
| `cluster` | 特征聚类编号；`-1` 表示参考库已直接认出（不消耗调用） |
| `candidate` | 大模型给的候选名（草稿，可能错） |
| `category` | 你要用的名字（apply 只认这个） |
| `confirmed` | apply 只写 `true` 的条目 |
| `note` | 跳过原因 / 说明 |

## 花费与取舍

- **认得出就不问**：参考库能直接分类的图不会调大模型。
- **同簇只问一次**：簇阈值 `vision.cluster_threshold`（默认 0.80）。
  阈值调高 → 簇更多 → **调用更多但不会把不同商品串成一个名字**；
  阈值调低 → 省调用 → 有把不同商品并簇、写错名字的风险。
  写错名字会污染参考库，所以默认取保守值。
- **失败不缓存、成功才缓存**（`cache/vision-cache.json`）：网络抖一下不会留下"这张图没名字"的假结论。
- **每日上限** `vision.daily_limit`：到点就不再调用，剩下的条目留空名、下次继续。
- 候选名只是草稿：模型可能读成瓶身上的子品牌（实测同一款红酒会被读成 `AUBA红酒` / `VUBA红酒` / `红酒`）。
  用 `aliases.json` 把别名归到一个正式名字，或在 `category` 里直接统一。

## 别名表

`aliases.json`：`正式名 → [别名...]`。`apply` 时按它归一，避免同款商品因叫法不同建出多个类别。
