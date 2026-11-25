# 由 deploy.sh 加载，不单独执行
#
# 服务由 systemd 托管：unit 里 User=普通用户，所以 ~/.local 里装的包装得到。
# run 是不装 unit 时的调试入口——板子前台直接跑一次，立刻看到报错。

cmd_install_service() {
    require_ssh
    require_rsync

    local py
    py=$(rsh "command -v '$BOARD_PYTHON'" | tr -d '\r')
    [ -n "$py" ] || die "板子上找不到 $BOARD_PYTHON"

    local tpl=$HERE/assets/wine-cooler.service.in
    [ -f "$tpl" ] || die "缺少 unit 模板: $tpl"

    local tmp
    tmp=$(mktemp)
    sed -e "s|@RUN_USER@|$BOARD_USER|g" \
        -e "s|@RUN_DIR@|$BOARD_DIR|g" \
        -e "s|@PYTHON@|$py|g" \
        -e "s|@MODULE@|$SERVICE_MODULE|g" \
        "$tpl" >"$tmp"

    rsync -a "$tmp" "$SSH_TARGET:/tmp/$SERVICE_NAME.service" || die "传 unit 失败"
    rm -f "$tmp"

    rroot "mv /tmp/$SERVICE_NAME.service '$UNIT_PATH' && systemctl daemon-reload && systemctl enable '$SERVICE_NAME'" \
        || die "安装 systemd unit 失败"
    log "已安装并设为开机自启: $UNIT_PATH"
    log "  ExecStart = $py -m $SERVICE_MODULE（以 $BOARD_USER 身份运行）"

    # 「重启设备」「改本机名字」都要以服务身份执行，各只授这一条命令的免密权限
    local sudoers=/etc/sudoers.d/$SERVICE_NAME
    rroot "printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /bin/systemctl reboot, /usr/bin/hostnamectl set-hostname *, /usr/bin/systemctl restart avahi-daemon, /bin/systemctl restart avahi-daemon\\n' '$BOARD_USER' > '$sudoers' && chmod 440 '$sudoers' && visudo -cf '$sudoers'" \
        || die "写 sudoers 失败"
    log "已授权重启设备、改本机名字、重启 mDNS 广播: $sudoers"

    rroot "{ printf '127.0.0.1 localhost\\n127.0.1.1 %s\\n::1 localhost ip6-localhost ip6-loopback\\n' \$(hostname); \
        grep -vE '^(127\\.|::1)' /etc/hosts || true; } > /tmp/hosts.new \
        && cat /tmp/hosts.new > /etc/hosts && rm -f /tmp/hosts.new" \
        || die "同步 /etc/hosts 失败"
    log "已把本机名字写进 /etc/hosts（回环段重建，其余条目保留）"

    # 板子只有 fe80 链路本地 IPv6，avahi 默认还会通过 IPv4 链路把 AAAA 发出去，
    # 客户端拿到后先白试一次 IPv6 才回退，所以两个开关都要关
    local avahi_conf=/etc/avahi/avahi-daemon.conf
    if rsh "test -f '$avahi_conf'" 2>/dev/null; then
        rroot "sed -i -e 's/^#*use-ipv6=.*/use-ipv6=no/' -e 's/^#*publish-aaaa-on-ipv4=.*/publish-aaaa-on-ipv4=no/' '$avahi_conf'; \
            grep -q '^use-ipv6=' '$avahi_conf' || echo 'use-ipv6=no' >> '$avahi_conf'; \
            grep -q '^publish-aaaa-on-ipv4=' '$avahi_conf' || echo 'publish-aaaa-on-ipv4=no' >> '$avahi_conf'; \
            systemctl restart avahi-daemon" \
            || die "关闭 avahi 的 IPv6 发布失败"
        log "已关闭 avahi 的 IPv6 发布，只广播 IPv4: $avahi_conf"
    fi

    # GPIO 只能走字符设备（sysfs 那套已废弃且设不了上下拉），默认只有 root 能开
    local gpio_rules=/etc/udev/rules.d/99-$SERVICE_NAME-gpio.rules
    rroot "printf '%s\\n' 'SUBSYSTEM==\"gpio\", KERNEL==\"gpiochip*\", GROUP=\"$BOARD_USER\", MODE=\"0660\"' > '$gpio_rules' && chmod 644 '$gpio_rules' && udevadm control --reload && udevadm trigger --subsystem-match=gpio" \
        || die "写 udev 规则失败"
    log "已放开 GPIO 设备权限: $gpio_rules（组 $BOARD_USER）"
}

cmd_uninstall_service() {
    require_ssh
    if unit_installed; then
        rroot "systemctl disable --now '$SERVICE_NAME'" >/dev/null 2>&1 || true
        rroot "rm -f '$UNIT_PATH' /etc/sudoers.d/$SERVICE_NAME /etc/udev/rules.d/99-$SERVICE_NAME-gpio.rules && systemctl daemon-reload && udevadm control --reload" || die "移除 unit 失败"
        log "已移除 $UNIT_PATH（含 sudoers 与 GPIO 规则）"
    else
        log "没有装 unit，无需移除"
    fi
}

_require_unit() {
    unit_installed || die "还没装 systemd unit，先跑: $0 install-service"
}

cmd_start() {
    require_ssh
    _require_unit
    rroot "systemctl start '$SERVICE_NAME'" || die "启动失败，看日志: $0 logs"
    sleep 1
    cmd_status
}

cmd_stop() {
    require_ssh
    _require_unit
    rroot "systemctl stop '$SERVICE_NAME'" || die "停止失败"
    log "已停止"
}

cmd_restart() {
    require_ssh
    _require_unit
    rroot "systemctl restart '$SERVICE_NAME'" || die "重启失败，看日志: $0 logs"
    sleep 1
    cmd_status
}

cmd_status() {
    require_ssh
    _require_unit
    rroot "systemctl is-enabled '$SERVICE_NAME'" 2>/dev/null | sed 's/^/  开机启动 /' || true
    if rroot "systemctl is-active --quiet '$SERVICE_NAME'"; then
        echo "  服务 运行中"
    else
        echo "  服务 未运行"
    fi
    rsh "if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ':$SERVICE_PORT '; then \
            echo '  端口 $SERVICE_PORT 监听中'; \
        else echo '  端口 $SERVICE_PORT 未监听'; fi"
}

cmd_logs() {
    require_ssh
    _require_unit
    local n=${1:-60}
    rroot "journalctl -u '$SERVICE_NAME' -n $n --no-pager"
}

cmd_run() {
    require_ssh
    log "在板子前台运行（Ctrl-C 退出）: $SERVICE_CMD"
    rsh "cd '$BOARD_DIR' && $SERVICE_CMD"
}
