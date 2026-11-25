# 由 deploy.sh 加载，不单独执行
#
# 契约的单一真相源是后端的 pydantic 模型：这里把它导出成 openapi.json，
# 再交给前端生成类型，最后构建出可以直接静态提供的单页。
# 前端的类型文件要提交（别人不跑后端也能编译），中间产物不提交。

cmd_contract() {
    [ -x "$PY" ] || die "缺少 $PY（先建 .venv）"
    [ -d "$FRONTEND" ] || die "缺少前端子仓库 $FRONTEND（git submodule update --init --recursive）"

    log "导出接口契约"
    "$PY" -m server.openapi --out "$FRONTEND/openapi.json" || die "导出契约失败"

    command -v npm >/dev/null || die "本机没有 npm"

    if [ ! -d "$FRONTEND/node_modules" ]; then
        log "前端依赖未安装，先装一次"
        ( cd "$FRONTEND" && npm ci --no-audit --no-fund ) || die "前端依赖安装失败"
    fi

    log "生成前端类型"
    ( cd "$FRONTEND" && npm run --silent gen:api ) || die "生成前端类型失败"

    log "构建前端单页"
    ( cd "$FRONTEND" && node tools/build-standalone.mjs wine-cooler ) || die "构建前端失败"
}
