#!/usr/bin/env python3
"""
使用 OPA (Open Policy Agent) 直接验证 OTLP 遥测数据。
作为 Weaver live-check 输入格式问题的替代方案。

用法:
    python3 validate_with_opa.py input.json

依赖: opa binary (brew install opa)
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
WEAVER_DIR = SCRIPT_DIR.parent
POLICIES_FILE = WEAVER_DIR / "policies" / "ai_agent_hooks.rego"


def extract_spans(otlp_data):
    """从 OTLP JSON 数据提取 spans，注入资源属性"""
    spans = []

    for rs in otlp_data.get("resourceSpans", []):
        resource_attrs = {}
        for attr in rs.get("resource", {}).get("attributes", []):
            key = attr["key"]
            value = attr["value"].get("stringValue") or \
                    attr["value"].get("intValue") or \
                    attr["value"].get("doubleValue")
            resource_attrs[key] = value

        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                # 注入资源属性
                existing_keys = {a["key"] for a in span.get("attributes", [])}
                for key, value in resource_attrs.items():
                    if key not in existing_keys and value is not None:
                        span["attributes"].append({
                            "key": key,
                            "value": {"stringValue": str(value)}
                        })
                spans.append(span)

    return spans


def run_opa_eval(input_data, query):
    """运行 OPA eval 命令"""
    proc = subprocess.run(
        ['opa', 'eval', '-d', str(POLICIES_FILE), '-I', query],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        timeout=10
    )
    if proc.returncode != 0:
        raise RuntimeError(f"OPA error: {proc.stderr}")
    return json.loads(proc.stdout)


def validate_span(span):
    """验证单个 span"""
    # OPA -I 直接使用输入作为 input 变量，无需包装
    input_data = span

    try:
        # 获取 violations
        violations_result = run_opa_eval(input_data, 'data.ai_agent_hooks.violations')
        violations = violations_result.get("result", [{}])[0].get("expressions", [{}])[0].get("value", [])

        # 获取 warnings
        warnings_result = run_opa_eval(input_data, 'data.ai_agent_hooks.warnings')
        warnings = warnings_result.get("result", [{}])[0].get("expressions", [{}])[0].get("value", [])

        # 获取 pass
        pass_result = run_opa_eval(input_data, 'data.ai_agent_hooks.pass')
        passed = pass_result.get("result", [{}])[0].get("expressions", [{}])[0].get("value", False)

        return {
            "violations": violations if isinstance(violations, list) else [],
            "warnings": warnings if isinstance(warnings, list) else [],
            "pass": bool(passed)
        }
    except Exception as e:
        return {"error": str(e), "violations": [], "warnings": [], "pass": False}


def print_report(results, spans):
    """打印验证报告"""
    total = len(results)
    passed = sum(1 for r in results if r["validation"].get("pass", False))
    failed = total - passed

    total_violations = sum(len(r["validation"].get("violations", [])) for r in results)
    total_warnings = sum(len(r["validation"].get("warnings", [])) for r in results)

    print("\n" + "=" * 70)
    print("  AI Agent Telemetry Validation Report")
    print("=" * 70)
    print(f"\n  Timestamp: {datetime.now().isoformat()}")
    print(f"  Total Spans: {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print(f"  Total Violations: {total_violations}")
    print(f"  Total Warnings: {total_warnings}")

    # 打印违规
    if total_violations > 0:
        print("\n" + "-" * 70)
        print("  ❌ VIOLATIONS")
        print("-" * 70)
        for r in results:
            violations = r["validation"].get("violations", [])
            if violations:
                print(f"\n  Span [{r['index']}]: {r['span_name']}")
                for v in violations:
                    print(f"    ❌ {v}")

    # 打印警告
    if total_warnings > 0:
        print("\n" + "-" * 70)
        print("  ⚠️  WARNINGS")
        print("-" * 70)
        for r in results:
            warnings = r["validation"].get("warnings", [])
            if warnings:
                print(f"\n  Span [{r['index']}]: {r['span_name']}")
                for w in warnings:
                    print(f"    ⚠️  {w}")

    print("\n" + "=" * 70)
    if failed == 0:
        print("  ✅ ALL VALIDATIONS PASSED")
    else:
        print(f"  ❌ {failed}/{total} SPANS FAILED VALIDATION")
    print("=" * 70 + "\n")

    return failed == 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate_with_opa.py <input.json>")
        sys.exit(1)

    input_file = Path(sys.argv[1])

    if not input_file.exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    if not POLICIES_FILE.exists():
        print(f"Error: Policy file not found: {POLICIES_FILE}")
        sys.exit(1)

    # 读取并提取 spans
    with open(input_file, 'r') as f:
        data = json.load(f)

    spans = extract_spans(data)
    print(f"📄 Loaded: {input_file}")
    print(f"📊 Extracted {len(spans)} spans\n")

    if not spans:
        print("No spans found in input file.")
        sys.exit(1)

    # 验证每个 span
    results = []
    for i, span in enumerate(spans):
        result = validate_span(span)
        results.append({
            "index": i,
            "span_name": span.get("name", "unknown"),
            "span_id": span.get("spanId", "unknown"),
            "parent_span_id": span.get("parentSpanId", ""),
            "validation": result
        })

    # 打印报告
    all_passed = print_report(results, spans)

    # 保存 JSON 报告
    report_file = input_file.with_suffix('.report.json')
    with open(report_file, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "input_file": str(input_file),
            "total_spans": len(spans),
            "passed": sum(1 for r in results if r["validation"].get("pass", False)),
            "failed": sum(1 for r in results if not r["validation"].get("pass", False)),
            "total_violations": sum(len(r["validation"].get("violations", [])) for r in results),
            "total_warnings": sum(len(r["validation"].get("warnings", [])) for r in results),
            "results": results
        }, f, indent=2, ensure_ascii=False)
    print(f"📝 Report saved: {report_file}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()