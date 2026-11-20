#!/usr/bin/env bash
# 用途: 批量标注 —— 本地聚类后用视觉大模型给每簇一个候选名，人工确认后写入参考库。
#
# 用法:
#   tools/label/run.sh scan  --crops <裁剪图目录> [--out tmp/labels.json] [--cluster 0.80] [--no-network]
#   tools/label/run.sh table --in tmp/labels.json
#   tools/label/run.sh apply --in tmp/labels.json
#
# 凭据: SILICONFLOW_API_KEY 走环境变量；没有或不加 --no-network 的网络不可用时，
#       scan 仍能跑完（全是空候选），apply 会跳过。
set -euo pipefail

# ── 路径与常量 ──
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/tools/.venv/bin/python"
CLI="$ROOT/tools/label/lib/cli.py"

# ── 函数 ──
check_deps() {
  if [ ! -x "$PY" ]; then
    echo "缺少 Python 环境: $PY" >&2
    echo "请先建: python3 -m venv tools/.venv && tools/.venv/bin/pip install -r tools/label/requirements.txt" >&2
    exit 2
  fi
}

# ── main ──
main() {
  check_deps
  cd "$ROOT"
  PYTHONPATH="$ROOT" "$PY" "$CLI" "$@"
}

main "$@"
