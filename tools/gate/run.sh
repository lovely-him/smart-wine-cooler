#!/usr/bin/env bash
# 用法: tools/gate/run.sh <子命令> [参数]
#   onnx-vs-rknn --images <目录> [--conf 0.15] [--rotate 90]   检测：宿主 onnx 与板卡 rknn 同图对比
#   embed-onnx-vs-rknn --images <目录>                        特征：同一批裁剪图的余弦相似度
#   本机配置（板卡地址、模型路径）写在 tools/gate/.env，模板见 .env.example
#   退出码: 0 通过 / 1 不通过 / 2 环境或参数问题
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=$ROOT/tools/.venv/bin/python
ENVFILE=$HERE/.env
GATE_SRC=$HERE/detect_json.py
CMP_SRC=$HERE/compare.py
EMBED_SRC=$HERE/embed_json.py
EMBED_CMP=$HERE/embed_compare.py
OUTDIR=$ROOT/cache/gate
DEFAULT_CONF=0.15
DEFAULT_ROTATE=90

# ── 函数 ──
usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

load_env() {
    [ -f "$ENVFILE" ] || return 0
    set -a
    # shellcheck disable=SC1090
    . "$ENVFILE"
    set +a
}

resolve() {
    BOARD_HOST=${BOARD_HOST:-192.168.64.18}
    BOARD_USER=${BOARD_USER:-kickpi}
    BOARD_ROOT=${BOARD_ROOT:-/home/kickpi/lab}
    BOARD_PYTHON=${BOARD_PYTHON:-python3}
    HOST_MODEL=${HOST_MODEL:-$ROOT/tools/embed_lab/models/yolov8n/yolov8n.onnx}
    BOARD_MODEL=${BOARD_MODEL:-$BOARD_ROOT/models/yolov8n_rk3588_int8_mmse.rknn}
    EMBED_HOST_MODEL=${EMBED_HOST_MODEL:-$ROOT/tools/embed_lab/models/mobilenetv2_pool.onnx}
    EMBED_BOARD_MODEL=${EMBED_BOARD_MODEL:-$BOARD_ROOT/models/mobilenetv2_pool_rk3588_int8.rknn}
    SSH="$BOARD_USER@$BOARD_HOST"
}

need_env() {
    local model=$1
    [ -x "$PY" ] || { printf '[FAIL] 缺少 venv: %s\n' "$PY" >&2; exit 2; }
    [ -f "$model" ] || { printf '[FAIL] 宿主模型不存在: %s\n' "$model" >&2; exit 2; }
}

need_board() {
    local model=$1
    ssh -o BatchMode=yes "$SSH" "test -f $model" 2>/dev/null || {
        printf '[FAIL] 板卡上找不到模型: %s\n       先推送: scp <rknn> %s:%s\n' \
            "$model" "$SSH" "$(dirname "$model")" >&2
        exit 2
    }
}

# 共享四步：宿主推理 → 推送 → 板卡推理 → 拉回比对
remote_gate() {
    local src=$1 cmp=$2 host_model=$3 board_model=$4 images=$5
    shift 5
    local extra=("$@")

    need_env "$host_model"
    need_board "$board_model"
    mkdir -p "$OUTDIR"
    local name
    name=$(basename "$(cd "$images" && pwd)")

    printf '\n== 1/4 宿主 ==\n'
    "$PY" "$src" --backend onnx --model "$host_model" \
        --images "$images" --out "$OUTDIR/host.json" "${extra[@]}"

    printf '\n== 2/4 推送脚本与图片 ==\n'
    ssh -o BatchMode=yes "$SSH" "mkdir -p $BOARD_ROOT/gate $BOARD_ROOT/img"
    tar czf - -C "$ROOT" services "tools/gate/$(basename "$src")" |
        ssh -o BatchMode=yes "$SSH" "tar xzf - -C $BOARD_ROOT"
    tar czf - -C "$(dirname "$images")" "$name" |
        ssh -o BatchMode=yes "$SSH" "tar xzf - -C $BOARD_ROOT/img"

    printf '\n== 3/4 板卡 ==\n'
    local remote_args=""
    local item
    for item in "${extra[@]}"; do remote_args+=" $item"; done
    ssh -o BatchMode=yes "$SSH" "cd $BOARD_ROOT && $BOARD_PYTHON tools/gate/$(basename "$src") \
        --backend rknn --model $board_model --images img/$name \
        --out gate/board.json$remote_args"

    printf '\n== 4/4 比对 ==\n'
    scp -q -o BatchMode=yes "$SSH:$BOARD_ROOT/gate/board.json" "$OUTDIR/board.json"
    "$PY" "$cmp" --ref "$OUTDIR/host.json" --test "$OUTDIR/board.json"
}

cmd_onnx_vs_rknn() {
    local images="" conf=$DEFAULT_CONF rotate=$DEFAULT_ROTATE
    while [ $# -gt 0 ]; do
        case "$1" in
            --images) images=${2:?--images 需要目录}; shift 2 ;;
            --conf)   conf=${2:?--conf 需要数值}; shift 2 ;;
            --rotate) rotate=${2:?--rotate 需要角度}; shift 2 ;;
            *) printf '[FAIL] 未知参数: %s\n' "$1" >&2; exit 2 ;;
        esac
    done
    [ -n "$images" ] || usage
    [ -d "$images" ] || { printf '[FAIL] 图片目录不存在: %s\n' "$images" >&2; exit 2; }

    remote_gate "$GATE_SRC" "$CMP_SRC" "$HOST_MODEL" "$BOARD_MODEL" "$images" \
        --conf "$conf" --rotate "$rotate"
}

cmd_embed_onnx_vs_rknn() {
    local images=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --images) images=${2:?--images 需要目录}; shift 2 ;;
            *) printf '[FAIL] 未知参数: %s\n' "$1" >&2; exit 2 ;;
        esac
    done
    [ -n "$images" ] || usage
    [ -d "$images" ] || { printf '[FAIL] 图片目录不存在: %s\n' "$images" >&2; exit 2; }

    remote_gate "$EMBED_SRC" "$EMBED_CMP" "$EMBED_HOST_MODEL" "$EMBED_BOARD_MODEL" "$images"
}

main() {
    [ $# -ge 1 ] || usage
    load_env
    resolve
    local cmd=$1
    shift
    case "$cmd" in
        onnx-vs-rknn)       cmd_onnx_vs_rknn "$@" ;;
        embed-onnx-vs-rknn) cmd_embed_onnx_vs_rknn "$@" ;;
        *)                  usage ;;
    esac
}

main "$@"
