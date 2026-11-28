# 柜内盘点相机

红酒柜的存量盘点器：关门触发一次观察，系统数出每层有什么，和上次人确认过的状态比，
把差异告诉人。

- **产品怎么设计**：`docs/smart-wine-cooler-wiki/`（独立仓库，不入库）
- **代码怎么组织**：[`docs/impl/代码地图.md`](docs/impl/代码地图.md)、[`docs/impl/代码结构.md`](docs/impl/代码结构.md)
- **选型与实现约定**：[`docs/impl/`](docs/impl/)
- **约束清单**：[`CLAUDE.md`](CLAUDE.md)

## 目录

| 路径 | 用途 |
| --- | --- |
| `server/` | 服务端：采集、识别、观察与提交管线、HTTP 接口、配置与库 |
| `frontend/` | 网页客户端（子模块，跟踪 `wine-cooler` 分支，只在宿主机构建） |
| `tools/deploy/` | 部署：导出契约、构建前端、同步到板子、装 systemd unit |
| `tools/check/` | 回归入口：语法与导入自检、分层依赖、文档编号对账、单元测试 |
| `tools/baseline/` | 接口回归快照：24 个用例的状态码与响应体与 `golden.json` 逐条比对 |
| `.githooks/` | 提交前自动跑全量检查（装一次：`git config core.hooksPath .githooks`） |
| `tools/convert/` | 模型转换（导出 ONNX / 转 rknn） |
| `tools/bench/` | 板端与宿主机性能基准 |
| `tools/gate/` | 两端一致性回归（同一张图在 onnx 与 rknn 上结果一致） |
| `models/` | 宿主机推理用的 onnx 模型（不入库） |
| `vendor/models/` | 板端推理用的 rknn 模型（不入库） |
| `docs/` | 知识库：产品设计（wiki）与实现约定（`docs/impl/`） |
| `tmp/` | 临时目录、冻结的旧工具、外部参考、样本、重构过程笔记（不入库） |

## 环境

```sh
python3 -m venv server/.venv
server/.venv/bin/pip install -r server/requirements-dev.txt   # host + pytest
```

工具链另有一个环境（模型转换、基准测试）：

```sh
python3 -m venv tools/.venv
tools/.venv/bin/pip install -r tools/requirements.txt
```

两个环境为什么分开、清单怎么分工，见 [`docs/impl/技术基线.md`](docs/impl/技术基线.md) §5。
要复现某台机器上的环境，装 `*-lock.txt` 而不是 `requirements*.txt`。

## 常用命令

```sh
git config core.hooksPath .githooks   # 装一次：以后每次提交前自动跑全量检查
./tools/check/check.sh all            # 手动跑一遍：语法导入 + 分层 + 文档编号 + 单元测试 + 接口快照
./tools/baseline/run.sh update        # 有意改接口后刷新接口快照
./tools/deploy/deploy.sh contract     # 导出契约 + 构建前端（不碰板子）
./tools/deploy/deploy.sh sync         # 同步到板子并装依赖
./tools/deploy/deploy.sh restart      # 重启服务
./tools/deploy/deploy.sh status       # 看板子上的运行状态
```

板子地址等本机配置在 `tools/deploy/.env`（`cp .env.example .env` 后填写，不入库）。
