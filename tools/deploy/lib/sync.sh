# 由 deploy.sh 加载，不单独执行

cmd_check() {
    require_ssh
    log "连通性 ok: $SSH_TARGET"
    rsh "printf '  主机 %s  架构 %s  python %s\n' \"\$(hostname)\" \"\$(uname -m)\" \"\$($BOARD_PYTHON -V 2>&1)\""
    rsh "df -h '$BOARD_DIR' 2>/dev/null | tail -1 | awk '{printf \"  磁盘 已用 %s / 共 %s（剩 %s）\n\", \$3, \$2, \$4}'"
    rsh "test -d '$BOARD_DIR' && echo '  部署目录已存在' || echo '  部署目录不存在，首次同步会创建'"
}

cmd_deps() {
    require_ssh

    # 板子 Python 是 PEP 668 externally-managed，连 --user 都被拦；
    # 而 rknnlite/numpy 本来就装在 ~/.local，只能沿这条路继续装。
    log "在板子上安装公共依赖"
    rsh "cd '$BOARD_DIR' && PIP_BREAK_SYSTEM_PACKAGES=1 $BOARD_PYTHON -m pip install --user -q -r server/requirements.txt" \
        || die "公共依赖安装失败"

    if [ -f "$ROOT/server/requirements-board.txt" ]; then
        log "在板子上安装板端专用依赖"
        rsh "cd '$BOARD_DIR' && PIP_BREAK_SYSTEM_PACKAGES=1 $BOARD_PYTHON -m pip install --user -q -r server/requirements-board.txt" \
            || warn "板端专用依赖没装全（NPU 相关通常由镜像预装，不影响）"
    fi

    # libgpiod 的绑定只有发行版包（PyPI 上的 gpiod 是另一套 v2 API），pip 装不到
    if ! rsh "$BOARD_PYTHON -c 'import gpiod, sys; sys.exit(0 if hasattr(gpiod, \"Chip\") else 1)' >/dev/null 2>&1"; then
        warn "板子缺 libgpiod 的 Python 绑定，门磁与柜内灯用不了；装一下: sudo apt install python3-gpiod"
    fi
}

cmd_sync() {
    require_rsync
    require_ssh
    [ -n "$SYNC_PATHS" ] || die "SYNC_PATHS 为空，检查 tools/deploy/.env"

    local p
    for p in $SYNC_PATHS; do
        [ -e "$ROOT/$p" ] || die "要同步的路径不存在: $p"
    done

    rsh "mkdir -p '$BOARD_DIR'" || die "创建远端目录失败"

    local synced=()
    for p in $SYNC_PATHS; do
        if [ -d "$ROOT/$p" ]; then
            rsh "mkdir -p '$BOARD_DIR/$p'" || die "创建远端目录失败: $p"
            rsync -a --delete \
                --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
                "$ROOT/$p/" "$SSH_TARGET:$BOARD_DIR/$p/" \
                || die "同步失败: $p"
        else
            rsync -a "$ROOT/$p" "$SSH_TARGET:$BOARD_DIR/" || die "同步失败: $p"
        fi
        synced+=("$p")
    done

    log "已同步: ${synced[*]}"
}
