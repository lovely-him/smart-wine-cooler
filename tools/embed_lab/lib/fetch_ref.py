"""从 Wikimedia Commons 抓图，按用途写进 data/ref 或 data/neg，并追加 manifest 记录。

只接受 JPEG/PNG。作者与许可证写进目标目录下的 CREDITS.jsonl。
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

# ── 路径与常量 ──
API = "https://commons.wikimedia.org/w/api.php"
UA = "rv1106-embed-lab/0.1 (local experiment)"
MIMES = ("image/jpeg", "image/png")
EXT = {"image/jpeg": ".jpg", "image/png": ".png"}


# ── 函数 ──
def api_get(params):
    query = dict(params, format="json")
    url = API + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def query(params, width):
    base = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": width,
    }
    base.update(params)
    data = api_get(base)
    pages = (data.get("query") or {}).get("pages") or {}
    hits = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        url = info.get("thumburl") or info.get("url")
        if mime not in MIMES or not url:
            continue
        meta = info.get("extmetadata") or {}
        hits.append(
            {
                "title": page.get("title", ""),
                "url": url,
                "mime": mime,
                "artist": (meta.get("Artist") or {}).get("value", ""),
                "license": (meta.get("LicenseShortName") or {}).get("value", ""),
            }
        )
    return hits


def by_search(text, limit, width):
    return query(
        {"generator": "search", "gsrsearch": text, "gsrnamespace": 6, "gsrlimit": limit},
        width,
    )


def by_category(name, limit, width):
    return query(
        {
            "generator": "categorymembers",
            "gcmtitle": f"Category:{name}",
            "gcmtype": "file",
            "gcmlimit": limit,
        },
        width,
    )


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as fp:
        fp.write(resp.read())
    return os.path.getsize(path)


def append_manifest(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fresh = {row["path"] for row in rows}
    kept = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("path") not in fresh:
                    kept.append(row)
    kept.extend(rows)
    with open(path, "w", encoding="utf-8") as fp:
        for row in kept:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True, choices=("ref", "neg"))
    parser.add_argument("--label", required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--out", required=True, help="data 根目录")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--width", type=int, default=900)
    args = parser.parse_args()

    target = os.path.join(args.out, args.split)
    os.makedirs(target, exist_ok=True)

    if args.category:
        hits = by_category(args.category, args.count, args.width)
    elif args.query:
        hits = by_search(args.query, args.count, args.width)
    else:
        print("[FAIL] 需要 --category 或 --query", file=sys.stderr)
        return 2
    if not hits:
        print(f"[FAIL] 未取到图片: {args.category or args.query}", file=sys.stderr)
        return 1

    credits_path = os.path.join(target, "CREDITS.jsonl")
    rows = []
    with open(credits_path, "a", encoding="utf-8") as cfp:
        for idx, hit in enumerate(hits, start=1):
            name = f"{args.label}_{idx:03d}{EXT[hit['mime']]}"
            path = os.path.join(target, name)
            try:
                size = download(hit["url"], path)
            except Exception as exc:
                print(f"  跳过 {hit['title']}: {exc}", file=sys.stderr)
                continue
            cfp.write(json.dumps(hit, ensure_ascii=False) + "\n")
            rows.append(
                {
                    "path": f"{args.split}/{name}",
                    "split": args.split,
                    "label": args.label if args.split == "ref" else "",
                    "source": "net",
                    "scene": "",
                    "note": hit["title"],
                }
            )
            print(f"  + {name} ({size} bytes) {hit['title']}")

    append_manifest(args.manifest, rows)
    print(f"[ OK ] 保存 {len(rows)} 张到 {target}，manifest 追加 {len(rows)} 条")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
