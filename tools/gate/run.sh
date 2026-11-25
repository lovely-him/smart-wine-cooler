#!/usr/bin/env bash
# 用法: tools/gate/run.sh <图片目录> [张数]
#   同一批图在宿主机（onnx）与板子（rknn）上各跑一遍：检测比框数与分数，特征比余弦。
#   脚本通过 ssh 的标准输入送过去，不在板子上留文件；只把图片临时拷进去。
# 本机配置（板子地址）读 tools/deploy/.env
# 退出码: 0 全部一致 / 1 有不一致 / 2 环境或参数问题
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=$ROOT/.venv/bin/python
ENVFILE=$ROOT/tools/deploy/.env
DETECT=$HERE/detect.py
EMBED=$HERE/embed.py
COMPARE=$HERE/compare.py
OUTDIR=$ROOT/cache/gate
REMOTE_IMAGE=/tmp/gate-image.jpg
REMOTE_OUT=/tmp/gate-out.json

# ── 函数 ──
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
}

die() { printf '[fail] %s\n' "$*" >&2; exit 2; }
log() { printf '[gate] %s\n' "$*"; }

load_env() {
    [ -f "$ENVFILE" ] || die "缺少 $ENVFILE"
    set -a
    # shellcheck disable=SC1090
    . "$ENVFILE"
    set +a
    [ -n "${BOARD_HOST:-}" ] || die "BOARD_HOST 为空"
    SSH_TARGET=$BOARD_USER@$BOARD_HOST
}

collect_images() {
    local dir=$1 limit=$2
    [ -d "$dir" ] || die "图片目录不存在: $dir"
    find "$dir" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) \
        | sort | head -n "$limit"
}

run_one() {
    local image=$1 name failed=0
    name=$(basename "$image")

    "$PY" "$DETECT" "$image" --backend onnx --out "$OUTDIR/host-d-$name.json" \
        2>"$OUTDIR/host-d-$name.err" || { warn_and_fail "宿主机检测失败"; return 1; }
    rsync -aq "$image" "$SSH_TARGET:$REMOTE_IMAGE" || return 1
    ssh -o BatchMode=yes "$SSH_TARGET" \
        "cd '$BOARD_DIR' && $BOARD_PYTHON - '$REMOTE_IMAGE' --backend rknn --out $REMOTE_OUT" \
        <"$DETECT" >/dev/null 2>"$OUTDIR/board-d-$name.err" \
        || { warn_and_fail "板子检测失败"; return 1; }
    rsync -aq "$SSH_TARGET:$REMOTE_OUT" "$OUTDIR/board-d-$name.json" || return 1

    "$PY" "$EMBED" "$image" --backend onnx --out "$OUTDIR/host-e-$name.json" \
        2>"$OUTDIR/host-e-$name.err" || { warn_and_fail "宿主机特征失败"; return 1; }
    ssh -o BatchMode=yes "$SSH_TARGET" \
        "cd '$BOARD_DIR' && $BOARD_PYTHON - '$REMOTE_IMAGE' --backend rknn --out $REMOTE_OUT" \
        <"$EMBED" >/dev/null 2>"$OUTDIR/board-e-$name.err" \
        || { warn_and_fail "板子特征失败"; return 1; }
    rsync -aq "$SSH_TARGET:$REMOTE_OUT" "$OUTDIR/board-e-$name.json" || return 1

    echo "--- $name"
    "$PY" "$COMPARE" "$OUTDIR/host-d-$name.json" "$OUTDIR/board-d-$name.json" || failed=1
    "$PY" "$COMPARE" "$OUTDIR/host-e-$name.json" "$OUTDIR/board-e-$name.json" || failed=1
    return "$failed"
}

warn_and_fail() {
    printf '[fail] %s（看 %s/*.err）\n' "$1" "$OUTDIR" >&2
}

# ── main ──
main() {
    [ $# -ge 1 ] || usage
    local dir=$1 limit=${2:-3}
    [ -x "$PY" ] || die "缺少 venv: $PY"

    load_env
    mkdir -p "$OUTDIR"

    local images failed=0
    images=$(collect_images "$dir" "$limit")
    [ -n "$images" ] || die "目录里没有图片: $dir"

    while IFS= read -r image; do
        run_one "$image" || failed=$((failed + 1))
    done <<<"$images"

    if [ "$failed" -ne 0 ]; then
        echo "结果：$failed 张不一致"
        exit 1
    fi
    echo "结果：全部一致"
}

main "$@"
