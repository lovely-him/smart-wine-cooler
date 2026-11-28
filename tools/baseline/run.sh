#!/usr/bin/env bash
# 用法: tools/baseline/run.sh <子命令>
#   check    当前代码跑一遍接口样本，与 golden.json 比对（默认）
#   update   重新采集并覆写 golden.json（有意改接口时才用）
#
# 用 server/.venv，可用 PY=... 覆盖
# 退出码: 0 一致 / 1 不一致 / 2 环境或参数问题
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PY:-$ROOT/server/.venv/bin/python}
SNAPSHOT=$HERE/lib/snapshot.py
GOLDEN=$HERE/golden.json
RUN=$ROOT/tmp/baseline-run

# ── 函数 ──
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
}

log() { printf '[baseline] %s\n' "$*"; }
die() { printf '[fail]  %s\n' "$*" >&2; exit 1; }

# ── main ──
main() {
    local sub=${1:-check}
    case $sub in
        check | update) ;;
        help | -h | --help) usage ;;
        *) die "未知子命令: $sub（用 --help 看用法）" ;;
    esac

    [ -x "$PY" ] || die "找不到 python: $PY（先建 server/.venv）"

    if [ "$sub" = "update" ]; then
        log "重新采集基线"
        "$PY" "$SNAPSHOT" "$ROOT" "$GOLDEN" "$RUN" --update
    else
        log "接口返回与 golden 比对"
        "$PY" "$SNAPSHOT" "$ROOT" "$GOLDEN" "$RUN"
    fi
}

main "$@"
