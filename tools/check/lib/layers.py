#!/usr/bin/env python3
"""分层依赖自检：数字大的可以 import 数字小的，反过来禁止。

规则见 `docs/impl/代码结构.md` §3。

用法: layers.py <仓库根>
退出码: 0 通过 / 1 有不许的依赖
"""

import ast
import sys
from pathlib import Path

# ── 路径与常量 ──
SERVER = Path("server")
SKIP_DIRS = {"tests", "__pycache__", ".venv"}
LAYERS = {
    "kernel": 0,
    "platform": 1,
    "vision": 2,
    "settings": 3,
    "catalog": 3,
    "observe": 4,
    "checkpoint": 4,
    "api": 5,
}
# 组合根与入口可以 import 任何东西
ROOTS = {"app.py", "main.py", "openapi.py"}
FORBIDDEN = (("api", "platform.db"),)


def _layer(name):
    return LAYERS.get(name)


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.lineno, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def check(root):
    server = root / SERVER
    problems = []
    for path in sorted(server.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        rel = path.relative_to(server)
        if len(rel.parts) == 1:
            if rel.name in ROOTS:
                continue
            own = None
        else:
            own = _layer(rel.parts[0])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, module in _imported_modules(tree):
            if not module.startswith("server."):
                continue
            parts = module.split(".")[1:]
            target = _layer(parts[0])
            if target is None:
                continue
            if own is not None and target > own:
                problems.append(
                    f"{rel}:{lineno}  层{own} 的 {rel.parts[0]} 反向依赖 层{target} 的 {parts[0]}（{module}）"
                )
            for importer, blocked in FORBIDDEN:
                if rel.parts[0] == importer and module.startswith(f"server.{blocked}"):
                    problems.append(f"{rel}:{lineno}  api 不许直接碰库（{module}）")
    return problems


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-3], file=sys.stderr)
        return 2
    problems = check(Path(argv[1]))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("  依赖方向全部向内")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
