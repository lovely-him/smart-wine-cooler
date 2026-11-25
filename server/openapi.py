"""导出接口契约，供前端生成类型。

用法：
    python3 -m server.openapi > openapi.json
    python3 -m server.openapi --out ../frontend/wine-cooler-openapi.json

前端只读生成结果，不手写数据结构的镜像——两边各写一份必然会漂。
"""

import argparse
import json
import sys

from server import models

SPEC_VERSION = "1"


def _models():
    return [getattr(models, name) for name in models.__all__]


def build():
    schemas = {}
    for model in _models():
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return {
        "openapi": "3.1.0",
        "info": {"title": "wine-cooler", "version": SPEC_VERSION},
        "paths": {},
        "components": {"schemas": schemas},
    }


def main():
    parser = argparse.ArgumentParser(description="导出接口契约")
    parser.add_argument("--out", help="输出文件，缺省写标准输出")
    args = parser.parse_args()

    text = json.dumps(build(), ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            fp.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
