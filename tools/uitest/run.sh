#!/usr/bin/env bash
# 用途: 验证网页刷新策略 —— 数据不变不重绘、表单编辑中不被覆盖、点「点此更新」强制应用。
# 依赖: node。用法: tools/uitest/run.sh
set -euo pipefail

# ── 路径与常量 ──
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CASE="$ROOT/tools/uitest/check_refresh.js"

# ── 函数 ──
need_node() {
  if ! command -v node >/dev/null 2>&1; then
    echo "缺少 node，无法运行前端检查。安装: sudo apt install nodejs" >&2
    exit 2
  fi
}

# ── main ──
main() {
  need_node
  node "$CASE"
}

main "$@"
