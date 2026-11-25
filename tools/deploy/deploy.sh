#!/usr/bin/env bash
# 用法: tools/deploy/deploy.sh <子命令> [参数]
#   check               检查板子连通性与环境
#   contract            导出契约、生成前端类型、构建前端单页
#   sync                同步代码到板子
#   deps                在板子上安装依赖
#   install-service     装 systemd unit 并设为开机自启
#   uninstall-service   移除 systemd unit
#   start / stop / restart
#   status              看开机启动、运行状态与端口
#   logs [N]            看 journal 日志，默认 60 行
#   run                 板子前台跑一次（不装 unit 时的调试入口）
#   exec <cmd>          在板子上执行命令
#   full                check + contract + sync + deps + install-service + restart + status
#
# 本机配置写在 tools/deploy/.env，模板见 tools/deploy/.env.example
# 退出码: 0 成功 / 1 失败 / 2 参数或配置问题
set -euo pipefail

# ── 路径与常量 ──
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
LIB=$HERE/lib

# ── 函数 ──
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 2
}

cmd_exec() {
    [ $# -gt 0 ] || die "exec 需要一条命令"
    rsh "$@"
}

cmd_full() {
    cmd_check
    cmd_contract
    cmd_sync
    cmd_deps
    cmd_install_service
    cmd_restart
}

# ── main ──
main() {
    # shellcheck source=lib/common.sh
    . "$LIB/common.sh"
    # shellcheck source=lib/contract.sh
    . "$LIB/contract.sh"
    # shellcheck source=lib/sync.sh
    . "$LIB/sync.sh"
    # shellcheck source=lib/service.sh
    . "$LIB/service.sh"

    local sub=${1:-}
    [ -n "$sub" ] || usage
    shift

    case $sub in
        help | -h | --help) usage ;;
    esac

    load_env

    case $sub in
        check)             cmd_check ;;
        contract)          cmd_contract ;;
        sync)              cmd_sync ;;
        deps)              cmd_deps ;;
        install-service)   cmd_install_service ;;
        uninstall-service) cmd_uninstall_service ;;
        start)             cmd_start ;;
        stop)              cmd_stop ;;
        restart)           cmd_restart ;;
        status)            cmd_status ;;
        logs)              cmd_logs "$@" ;;
        run)               cmd_run ;;
        exec)              cmd_exec "$@" ;;
        full)              cmd_full ;;
        *)                 die "未知子命令: $sub（用 --help 看用法）" ;;
    esac
}

main "$@"
