#!/usr/bin/env bash
# 用法: ./tools/embed_lab/run.sh <子命令> [参数]
#   setup                              建 venv、装依赖、下载模型（只需一次）
#   check                              自检依赖、模型、数据与 manifest
#   fetch-ref <标签> <数量> [分类名]    抓网络图作参考图（给 Commons 分类名更准）
#   fetch-neg <标签> <数量> [分类名]    抓非目标物体作负样本
#   detect <图片目录> [旋转角]          检测瓶子、报数量与位置、裁剪落库（默认旋转 90°）
#   eval                               参考库 → 查询集的检索效果
#   PIP_INDEX 可覆盖 pip 镜像源（默认清华）
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
TOOLS=$(cd "$HERE/.." && pwd)
LIB=$HERE/lib
VENV=$TOOLS/.venv
PY=$VENV/bin/python
MODEL=$HERE/models/mobilenetv2.onnx
POOL=$HERE/models/mobilenetv2_pool.onnx
DET=$HERE/models/yolov8n/yolov8n.onnx
DATA=$HERE/data
OUT=$HERE/out
MANIFEST=$DATA/manifest.jsonl
PIP_INDEX=${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}
PIP_CONF=$VENV/pip.conf

# ── 函数 ──
usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

need_venv() {
    [ -x "$PY" ] || { printf '[FAIL] 未初始化，先执行: %s setup\n' "$0" >&2; exit 1; }
}

cmd_setup() {
    [ -x "$PY" ] || python3 -m venv "$VENV"
    [ -f "$PIP_CONF" ] || printf '[global]\nindex-url = %s\n' "$PIP_INDEX" > "$PIP_CONF"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r "$TOOLS/requirements.txt"
    mkdir -p "$(dirname "$MODEL")" "$DATA/ref" "$DATA/query" "$DATA/neg" "$DATA/crop" "$OUT"
    [ -f "$MANIFEST" ] || : > "$MANIFEST"
    [ -s "$MODEL" ] || "$PY" "$LIB/fetch_model.py" --out "$MODEL"
    [ -s "$POOL" ] || "$PY" "$LIB/export_tap.py" --in "$MODEL" --out "$POOL"
    [ -s "$DET" ] || "$PY" "$LIB/fetch_detector.py" --out "$DET"
    printf '[ OK ] setup 完成\n'
}

cmd_check() {
    need_venv
    "$PY" "$LIB/env_check.py" --model "$MODEL" --data "$DATA" --manifest "$MANIFEST"
}

cmd_fetch() {
    local split=$1 label=$2 count=$3 category=${4:-}
    need_venv
    if [ -n "$category" ]; then
        "$PY" "$LIB/fetch_ref.py" --split "$split" --label "$label" --count "$count" \
            --out "$DATA" --manifest "$MANIFEST" --category "$category"
    else
        "$PY" "$LIB/fetch_ref.py" --split "$split" --label "$label" --count "$count" \
            --out "$DATA" --manifest "$MANIFEST" --query "$label"
    fi
}

cmd_detect() {
    local src=${1:?需要图片目录} rotate=${2:-90}
    need_venv
    mkdir -p "$OUT/detect"
    "$PY" "$LIB/detect_crop.py" --model "$DET" --in "$src" --rotate "$rotate" \
        --out "$DATA/crop" --annot "$OUT/detect" \
        --manifest "$MANIFEST" --report "$OUT/detect/report.json"
}

cmd_eval() {
    need_venv
    "$PY" "$LIB/embed_eval.py" --model "$MODEL" --data "$DATA" \
        --manifest "$MANIFEST" --out "$OUT/report.json"
}

main() {
    [ $# -ge 1 ] || usage
    local cmd=$1
    shift
    case "$cmd" in
        setup)     cmd_setup ;;
        check)     cmd_check ;;
        fetch-ref) [ $# -ge 2 ] || usage; cmd_fetch ref "$@" ;;
        fetch-neg) [ $# -ge 2 ] || usage; cmd_fetch neg "$@" ;;
        detect)    [ $# -ge 1 ] || usage; cmd_detect "$@" ;;
        eval)      cmd_eval ;;
        *)         usage ;;
    esac
}

main "$@"
