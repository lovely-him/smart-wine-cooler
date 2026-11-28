#!/usr/bin/env bash
# 用法: tools/check/check.sh <子命令>
#   quick    语法、导入与分层自检（秒级，改完就绪跑这个）
#   docs     文档自检（配置编号对账、功能编号与跨页引用；改 wiki 就跑）
#   unit     单元测试（server/tests 存在时才真跑）
#   baseline 接口快照：24 个用例的状态码与响应体与 golden 逐条比对
#   all      quick + docs + unit + baseline
#
# 用 server/.venv，可用 PY=... 覆盖
# 退出码: 0 通过 / 1 不通过 / 2 环境或参数问题
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PY:-$ROOT/server/.venv/bin/python}
PKG=$ROOT/server
TESTS=$PKG/tests
DOCS=$HERE/lib/docs_links.py
LAYERS=$HERE/lib/layers.py
BASELINE=$ROOT/tools/baseline/run.sh

# ── 函数 ──
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
}

log()  { printf '[check] %s\n' "$*"; }
die()  { printf '[fail]  %s\n' "$*" >&2; exit 1; }

require_python() {
    [ -x "$PY" ] || die "找不到 python: $PY（先建 server/.venv）"
}

cmd_quick() {
    require_python
    log "语法与导入"
    "$PY" -m compileall -q "$PKG" || die "语法错误"
    "$PY" - "$ROOT" "$PKG" <<'EOF' || die "导入失败"
import importlib
import pkgutil
import sys

root, pkg = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)

name = pkg.rsplit("/", 1)[-1]
package = importlib.import_module(name)
names = [name] + [m.name for m in pkgutil.walk_packages(package.__path__, prefix=f"{name}.")]

failed = []
for mod in names:
    try:
        importlib.import_module(mod)
    except Exception as exc:
        failed.append(f"{mod}: {exc.__class__.__name__}: {exc}")
if failed:
    print("\n".join(failed), file=sys.stderr)
    sys.exit(1)
print(f"  已导入 {len(names)} 个模块")
EOF
    log "分层依赖"
    "$PY" "$LAYERS" "$ROOT" || die "依赖方向不对（规则见 docs/impl/代码结构.md §3）"
    log "通过"
}

cmd_docs() {
    require_python
    log "文档编号与引用"
    "$PY" "$DOCS" "$ROOT" || die "文档自检不通过（规则见 docs/smart-wine-cooler-wiki/00-文档规范.md）"
}

cmd_unit() {
    require_python
    if [ ! -d "$TESTS" ]; then
        log "还没有单元测试（$TESTS 不存在），跳过"
        return 0
    fi
    "$PY" -m pytest -q "$TESTS" || die "单元测试不通过"
}

cmd_baseline() {
    require_python
    log "接口快照"
    [ -x "$BASELINE" ] || die "找不到 $BASELINE"
    "$BASELINE" check || die "接口返回与 golden 不一致（有意改接口就跑 tools/baseline/run.sh update）"
}

# ── main ──
main() {
    local sub=${1:-}
    [ -n "$sub" ] || usage
    shift

    case $sub in
        quick)   cmd_quick ;;
        docs)    cmd_docs ;;
        unit)    cmd_unit ;;
        baseline) cmd_baseline ;;
        all)     cmd_quick; cmd_docs; cmd_unit; cmd_baseline ;;
        help | -h | --help) usage ;;
        *)       die "未知子命令: $sub（用 --help 看用法）" ;;
    esac
}

main "$@"
