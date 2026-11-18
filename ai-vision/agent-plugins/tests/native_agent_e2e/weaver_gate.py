"""Validate the complete native matrix's original OTLP, including validator health."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from run_suite import DEFAULT_CASES, VERSIONS
from verify import verify_case
from source_acceptance import assess_source, bounded_row
from evidence_integrity import exports_match_raw

SIGNALS = {
    "traces": ("resourceSpans", "scopeSpans", "spans"),
    "logs": ("resourceLogs", "scopeLogs", "logRecords"),
    "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
}


def collect_matrix(directory: Path):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    expected = {app + "-" + scenario for app, scenario in DEFAULT_CASES}
    cases = summary.get("cases", [])
    names = [case.get("case") for case in cases]
    if (summary.get("status") not in ("passed", "passed_with_known_limitations") or summary.get("mode") != "fake"
            or summary.get("scope") != "full" or summary.get("expected_cases") != len(expected)
            or summary.get("executed_cases") != len(expected) or len(names) != len(expected)
            or set(names) != expected or not all(case.get("source_verified") is True or bounded_row(case) for case in cases)):
        raise ValueError("Weaver gate requires the complete successful native fake49 source matrix")
    declared_status = 'passed_with_known_limitations' if any(bounded_row(case) for case in cases) else 'passed'
    if declared_status == 'passed_with_known_limitations':
        if (type(summary.get('verified_cases')) is not int or summary['verified_cases'] != sum(row.get('verified') is True for row in cases)
                or type(summary.get('known_limitation_cases')) is not int or summary['known_limitation_cases'] != sum(bounded_row(row) for row in cases)
                or type(summary.get('failed_cases')) is not int or summary['failed_cases'] != 0):
            raise ValueError("Native summary acceptance counts do not match the source inventory")
    if summary.get('status') != declared_status:
        raise ValueError("Native summary status does not match the declared source acceptance")
    envelopes, samples = [], 0
    for name in sorted(expected):
        # Ignore absolute paths from the original runner. Artifacts are movable;
        # only the fixed expected case directories may supply gate inputs.
        path = directory / name / "evidence.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        app, scenario = name.split("-", 1)
        if case.get("case") != name or case.get("app") != app or case.get("scenario") != scenario or case.get("mode") != "fake" or case.get("version") != VERSIONS[app]:
            raise ValueError("Native evidence identity or pinned runtime does not match " + name)
        if not exports_match_raw(case.get('exports')):
            raise ValueError("Original OTLP differs from source facts for " + name)
        source = verify_case(app=app, scenario=scenario, model="native-e2e-chat", receipt=case["receipt"], mode=case['mode'],
            result=case["result"], requests=case["requests"], exports=case["exports"], expected_resource=case["expected_resource"])
        if not source["verified"]:
            assessment = assess_source(case, strict=source)
            row = next(row for row in cases if row['case'] == name)
            if (not assessment['accepted'] or not bounded_row(row)
                    or case.get('source', {}).get('verified') is not False or case.get('verified') is not False
                    or case.get('status') != 'passed_with_known_limitations'
                    or case.get('canonical_verified') is not True or case.get('provider_reconciled') is not False
                    or case.get('known_limitations') != assessment['known_limitations']):
                raise ValueError("Original source evidence fails independent verification for " + name)
        present = set()
        for export in case["exports"]:
            signal = export.get("signal")
            raw = export.get("otlp")
            if export.get("admitted") is not True or signal not in SIGNALS or not isinstance(raw, dict):
                raise ValueError("Native export is rejected or lacks original OTLP for " + name)
            root, scope, rows = SIGNALS[signal]
            if set(raw).intersection(key[0] for key in SIGNALS.values()) != {root}:
                raise ValueError("Original OTLP signal identity does not match " + name)
            count = sum(len(group.get(rows, [])) for resource in raw.get(root, []) for group in resource.get(scope, []))
            if not count:
                raise ValueError("Original OTLP signal has no samples for " + name)
            envelopes.append(raw)
            samples += count
            present.add(signal)
        if present != set(SIGNALS):
            raise ValueError("Every native case requires original traces, logs and metrics")
    return envelopes, samples


def check_report(report, stderr: str, returncode: int, *, signals: int, samples: int):
    summary = report.get("summary", {})
    # The underlying CLI can succeed on empty inputs and can log an evaluation
    # failure without returning it as a violation. Neither establishes a pass.
    counts = ("total_signals", "total_samples", "violations", "semconv_issues")
    if (returncode != 0 or "Validation error" in stderr or "skipping" in stderr.lower()
            or not all(type(summary.get(key)) is int for key in counts)
            or summary.get("total_signals") != signals or summary.get("total_samples") != samples
            or summary.get("violations") != 0 or summary.get("semconv_issues") != 0
            or summary.get("pass") is not True or signals <= 0 or samples <= 0):
        raise ValueError("Weaver did not validate every original sample without errors or violations")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, action="append")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--weaver-command-json", default='["weaver-guard"]')
    args = parser.parse_args(argv)
    command = json.loads(args.weaver_command_json)
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("Supply a nonempty Weaver argv list")
    args.out.mkdir(parents=True, exist_ok=False)
    envelopes, samples = [], 0
    for directory in args.input_dir:
        original, count = collect_matrix(directory)
        envelopes.extend(original)
        samples += count
    payload = args.out / "original-otlp.jsonl"
    payload.write_text("".join(json.dumps(raw, separators=(",", ":"), allow_nan=False) + "\n" for raw in envelopes), encoding="utf-8")
    result = subprocess.run([*command, "validate", str(payload), "--policies-dir",
        str(args.repo / "ai-vision/agent-plugins/weaver/policies"), "--semconv-dir",
        str(args.repo / "ai-vision/agent-plugins/docs/semconv"), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=300, check=False)
    (args.out / "weaver.json").write_text(result.stdout, encoding="utf-8")
    (args.out / "weaver.log").write_text(result.stderr, encoding="utf-8")
    report = json.loads(result.stdout)
    check_report(report, result.stderr, result.returncode, signals=len(envelopes), samples=samples)
    print(json.dumps({"status": "passed", "matrices": len(args.input_dir), "signals": len(envelopes), "samples": samples}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
