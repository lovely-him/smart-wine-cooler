#!/usr/bin/env python3
"""文档自检：配置编号、功能编号、跨页引用。

规则见 `docs/smart-wine-cooler-wiki/00-文档规范.md`：
- §4 引用写成「页代码 §节号」，页与节必须真实存在
- §5 配置编号必须与 `server/platform/settings_def.py` 的 `ITEMS` 逐条一致；
     功能编号被引用就必须被定义，且同一编号只在一页里定义

用法: docs_links.py <仓库根>
退出码: 0 通过 / 1 不通过
"""

import re
import sys
from collections import Counter
from pathlib import Path

# ── 路径与常量 ──
WIKI_DIR = Path("docs/smart-wine-cooler-wiki")
DEFAULTS_PY = Path("server/platform/settings_def.py")

CONFIG_PREFIXES = ("NET", "CAM", "TRIG", "JUDGE", "AI", "DATA", "SEC", "SYS")
FEATURE_PREFIXES = ("BLD", "OBS", "DIF", "CAT", "HIS", "MNT")

ID_PATTERN = r"(?:{})-\d\d".format("|".join(CONFIG_PREFIXES + FEATURE_PREFIXES))
HEADING_ID = re.compile(rf"^#{{2,4}}.*?`({ID_PATTERN})`", re.M)
ROW_ID = re.compile(rf"^\|\s*`({ID_PATTERN})`\s*\|", re.M)
SETTING_DEF = re.compile(rf'SettingItemDef\(\s*"({ID_PATTERN})"')
ANY_ID = re.compile(rf"`({ID_PATTERN})`")
SECTION_REF = re.compile(r"`([A-Z]{2,3}) §(\d+(?:\.\d+)*)")
HEADING_NO = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)*)[.\s]", re.M)


# ── 函数 ──
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def page_files(wiki: Path) -> dict[str, Path]:
    """页代码 → 文件。文件名形如 flow-02-OBS-观察.md / cross-04-TM-....md"""
    pages = {}
    for path in sorted(wiki.glob("*.md")):
        parts = path.name.split("-")
        if parts[0] in ("flow", "cross") and len(parts) > 2:
            pages[parts[2]] = path
    return pages


def check_config_ids(root: Path, wiki: Path, failures: list[str]) -> int:
    """SET 页上的配置编号 === defaults.py 的 ITEMS。"""
    set_page = next(wiki.glob("cross-01-SET-*.md"), None)
    if set_page is None:
        failures.append("找不到 SET 页（cross-01-SET-*.md）")
        return 0
    doc_ids = {i for i in ANY_ID.findall(read(set_page)) if i.split("-")[0] in CONFIG_PREFIXES}
    code_ids = set(SETTING_DEF.findall(read(root / DEFAULTS_PY)))

    missing = sorted(code_ids - doc_ids)
    extra = sorted(doc_ids - code_ids)
    if missing:
        failures.append(f"SET 缺编号（代码里有）: {', '.join(missing)}")
    if extra:
        failures.append(f"SET 多编号（代码里没有）: {', '.join(extra)}")
    return len(code_ids)


def check_feature_ids(wiki: Path, failures: list[str]) -> int:
    """功能编号：被引用就必须被定义；同一页里只能有一个小节、一行表格。"""
    pages = page_files(wiki)
    heading_pages: dict[str, set[str]] = {}
    row_pages: dict[str, set[str]] = {}
    headings: Counter[str] = Counter()
    rows: Counter[str] = Counter()

    for code, path in pages.items():
        text = read(path)
        for match in HEADING_ID.findall(text):
            if match.split("-")[0] in FEATURE_PREFIXES:
                heading_pages.setdefault(match, set()).add(code)
                headings[match] += 1
        for match in ROW_ID.findall(text):
            if match.split("-")[0] in FEATURE_PREFIXES:
                row_pages.setdefault(match, set()).add(code)
                rows[match] += 1

    referenced: dict[str, set[str]] = {}
    for path in sorted(wiki.glob("*.md")):
        for match in ANY_ID.findall(read(path)):
            if match.split("-")[0] in FEATURE_PREFIXES:
                referenced.setdefault(match, set()).add(path.name)

    defined = set(heading_pages) | set(row_pages)
    for item, where in sorted(referenced.items()):
        if item not in defined:
            failures.append(f"{'、'.join(sorted(where))}: 引用未定义的功能编号 {item}")
    for item in sorted(defined):
        where = heading_pages.get(item, set()) | row_pages.get(item, set())
        if len(where) > 1:
            failures.append(f"功能编号 {item} 在 {'、'.join(sorted(where))} 重复定义")
        elif headings[item] > 1:
            failures.append(f"功能编号 {item} 在 {next(iter(where))} 有两个小节")
        elif rows[item] > 1:
            failures.append(f"功能编号 {item} 在 {next(iter(where))} 有两行表格")
    return len(defined)


def check_section_refs(wiki: Path, failures: list[str]) -> int:
    """`CODE §N` 与 `§N.M` 必须指向存在的节。"""
    pages = page_files(wiki)
    sections = {code: set(HEADING_NO.findall(read(path))) for code, path in pages.items()}

    total = 0
    for path in sorted(wiki.glob("*.md")):
        for code, number in SECTION_REF.findall(read(path)):
            total += 1
            if code not in pages:
                failures.append(f"{path.name}: 未知页 {code}")
            elif number not in sections[code]:
                failures.append(f"{path.name}: {code} §{number} 不存在")
    return total


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    wiki = root / WIKI_DIR
    if not wiki.is_dir():
        print(f"[fail]  找不到 {wiki}", file=sys.stderr)
        return 1

    failures: list[str] = []
    configs = check_config_ids(root, wiki, failures)
    features = check_feature_ids(wiki, failures)
    refs = check_section_refs(wiki, failures)

    if failures:
        for item in failures:
            print(f"[fail]  {item}", file=sys.stderr)
        return 1
    print(f"  配置编号 {configs} 项、功能编号 {features} 项、跨页引用 {refs} 处，全部对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
