"""给 ONNX 模型追加一个中间张量作为输出，用于取池化特征。"""

import argparse
import sys

import onnx

# ── 路径与常量 ──
DEFAULT_TAP = "/mobilenet_v2/pooler/GlobalAveragePool_output_0"


# ── 函数 ──
def resolve_tap(graph, wanted):
    produced = {out for node in graph.node for out in node.output}
    if wanted and wanted in produced:
        return wanted
    for node in graph.node:
        if node.op_type in ("GlobalAveragePool", "ReduceMean"):
            return node.output[0]
    raise RuntimeError("图中找不到池化输出")


# ── main ──
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--node", default=DEFAULT_TAP)
    args = parser.parse_args()

    model = onnx.load(args.src)
    tap = resolve_tap(model.graph, args.node)
    if tap not in [out.name for out in model.graph.output]:
        extra = onnx.ValueInfoProto()
        extra.name = tap
        model.graph.output.append(extra)
    onnx.save(model, args.out)
    print(f"model: {args.out} (tap={tap})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
