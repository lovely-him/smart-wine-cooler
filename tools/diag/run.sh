#!/usr/bin/env bash
# 用途: 列出诊断工具。这些脚本只在"检不出/分数不对"时用，不是常规检查。
# 依赖: 见各脚本（宿主用 tools/.venv；板端用系统 python + rknnlite）。用法: tools/diag/run.sh
set -euo pipefail

# ── 路径与常量 ──
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ── main ──
main() {
  cat <<'EOF'
诊断工具（都在仓库根执行，宿主或板端都能跑）

  detect_probe.py <图片> [conf]
      四个方向各跑一遍 + 合并去重。看"模型能不能认出"与"分数差多少"。

  single_probe.py <图片> [conf]
      把每个目标逐个孤立后测四个方向。区分"角度敏感"与"目标本身难检"。

  nms_probe.py <图片> <角度> [conf]
      对比正常 NMS 与关掉抑制后的候选。区分"模型给低分"与"被 NMS 抑制"。

  cmp_models.py <模型1> <模型2> ... <图片...>
      同一批图比多个模型：检出数 / 最高分 / 单帧耗时。

典型排查顺序：
  1. cmp_models 看换模型有没有用
  2. detect_probe 看是角度问题还是分数问题
  3. single_probe 看是不是旁边的目标在压制它
  4. nms_probe 排除 NMS 抑制
EOF
  echo
  echo "仓库根: $ROOT"
}

main "$@"
