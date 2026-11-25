# 回归入口
#
# 每改完一处就跑一次 quick（秒级），防止改东坏西。
# 子命令见 --help。

---

## 什么时候跑什么

| 场景 | 跑什么 |
| --- | --- |
| 改完代码，想确认没写错 | `./tools/check/check.sh quick` |
| 提交前 | `./tools/check/check.sh all` |
| 部署到板子之后 | `./tools/deploy/deploy.sh status` + 界面看一眼 |

## 关于单元测试

`unit` 跑 `server/tests/`（pytest）。该目录不存在时只提示一句并以 0 退出，不阻塞流程。
