#!/usr/bin/env python3
"""
从 OTLP ExportTraceServiceRequest JSON 中提取 span 实体。
用于 Weaver live-check 验证。

Weaver live-check 期望 span 格式为直接对象:
    {"name": "...", "attributes": [...], "kind": 1, ...}

资源属性需要通过其他方式访问（如自定义字段或注入到 attributes）。

用法:
    python3 extract_spans.py < input.json > output.json
    python3 extract_spans.py input.json output.json

输出格式: JSON 数组，每个元素是一个 span 对象
"""

import json
import sys


def extract_spans(data):
    """从 OTLP JSON 中提取 span 对象，并注入资源属性"""
    spans = []

    for rs in data.get("resourceSpans", []):
        resource_attrs = {}
        for attr in rs.get("resource", {}).get("attributes", []):
            key = attr["key"]
            value = attr["value"].get("stringValue") or \
                    attr["value"].get("intValue") or \
                    attr["value"].get("doubleValue")
            resource_attrs[key] = value

        for ss in rs.get("scopeSpans", []):
            scope_info = ss.get("scope", {})

            for span in ss.get("spans", []):
                # 将资源属性注入到 span.attributes 中（如果不存在）
                # 这样策略可以访问这些属性
                existing_keys = {a["key"] for a in span.get("attributes", [])}

                for key, value in resource_attrs.items():
                    if key not in existing_keys and value is not None:
                        span["attributes"].append({
                            "key": key,
                            "value": {"stringValue": str(value)}
                        })

                # 添加 scope 信息
                if scope_info:
                    existing_keys = {a["key"] for a in span.get("attributes", [])}
                    if "scope.name" not in existing_keys and scope_info.get("name"):
                        span["attributes"].append({
                            "key": "scope.name",
                            "value": {"stringValue": scope_info["name"]}
                        })

                spans.append(span)

    return spans


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            data = json.load(f)
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        data = json.load(sys.stdin)
        output_file = None

    spans = extract_spans(data)
    output = json.dumps(spans, ensure_ascii=False, indent=2)

    if output_file:
        with open(output_file, 'w') as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()