# 由 deploy.sh 加载，不单独执行

log()  { printf '[deploy] %s\n' "$*"; }
warn() { printf '[warn]   %s\n' "$*" >&2; }
die()  { printf '[fail]   %s\n' "$*" >&2; exit 1; }

load_env() {
    local f=$HERE/.env
    [ -f "$f" ] || die "缺少配置 $f —— 先复制 .env.example 为 .env 并填写"
    set -a
    # shellcheck disable=SC1090
    . "$f"
    set +a

    : "${BOARD_HOST:?deploy/.env 里 BOARD_HOST 为空}"
    : "${BOARD_USER:?deploy/.env 里 BOARD_USER 为空}"
    : "${BOARD_DIR:?deploy/.env 里 BOARD_DIR 为空}"
    : "${SERVICE_NAME:?tools/deploy/.env 里 SERVICE_NAME 为空}"
    : "${SERVICE_MODULE:?tools/deploy/.env 里 SERVICE_MODULE 为空}"

    SSH_TARGET=$BOARD_USER@$BOARD_HOST
    SERVICE_CMD="$BOARD_PYTHON -m $SERVICE_MODULE"
    UNIT_PATH=/etc/systemd/system/$SERVICE_NAME.service
    PY=${PY:-$ROOT/.venv/bin/python}
    FRONTEND=${FRONTEND:-$ROOT/frontend}
    SYNC_PATHS=${SYNC_PATHS:-}
}

rsh() {
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$SSH_TARGET" "$@"
}

# 提权操作走 root 直连：sudo 要的是当前用户的密码，而 root 密码只对 root 登录有效
rroot() {
    [ -n "${BOARD_ROOT_PASSWORD:-}" ] || die "tools/deploy/.env 里 BOARD_ROOT_PASSWORD 为空"
    command -v sshpass >/dev/null || die "本机没有 sshpass（提权需要它传密码）"
    sshpass -p "$BOARD_ROOT_PASSWORD" \
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 \
        "$BOARD_ROOT_USER@$BOARD_HOST" "$@"
}

unit_installed() {
    rsh "test -f '$UNIT_PATH'" 2>/dev/null
}

require_ssh() {
    command -v ssh >/dev/null || die "本机没有 ssh"
    rsh true 2>/dev/null || die "连不上 $SSH_TARGET（先确认板子在线、已配免密）"
}

require_rsync() {
    command -v rsync >/dev/null || die "本机没有 rsync"
}
