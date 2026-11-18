#!/usr/bin/env python3
"""Finalize a Hermes golden provenance manifest from verified local artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCENARIO_ORDER = (
    "basic-llm", "tool-success", "tool-error", "multi-tool", "subagent",
    "mcp-tool", "skill-call", "reset/finalize", "api-error", "compaction",
)
REQUIRED_SCENARIOS = frozenset({
    "basic-llm", "tool-success", "tool-error", "multi-tool", "subagent", "reset/finalize",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def plugin_version(path: Path) -> str:
    match = re.search(r'^version:\s*["\']?([^"\'\s]+)', path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise ValueError(f"plugin version missing from {path}")
    return match.group(1)


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def lifecycle_transcripts(directory: Path) -> list[Path]:
    ordered = sorted(directory.glob("session_*.jsonl"))
    hooks_by_path = {
        path: {record.get("hook") for record in read_records(path)}
        for path in ordered
    }
    matches: set[Path] = set()
    for index, path in enumerate(ordered):
        if "on_session_reset" not in hooks_by_path[path]:
            continue
        matches.add(path)
        # /new rotates the canonical transcript. Preserve the immediately
        # preceding completed session as evidence that reset finalized the old
        # session and did not fabricate a terminal event for the new one.
        for candidate in reversed(ordered[:index]):
            hooks = hooks_by_path[candidate]
            if "on_session_start" in hooks and "on_session_finalize" in hooks:
                matches.add(candidate)
                break
    return sorted(matches)


def replay_paths(transcript: Path, replay_dir: Path) -> list[str]:
    suffix = transcript.stem
    paths = [
        replay_dir / f"{prefix}_{suffix}{extension}"
        for prefix, extension in (
            ("spans", ".jsonl"), ("events", ".jsonl"), ("metrics", ".jsonl"),
            ("diagnostics", ".ndjson"),
        )
    ]
    return [str(path) for path in paths if path.is_file()]


def tool_id_counts(transcripts: list[Path]) -> dict[str, int]:
    native: set[str] = set()
    synthetic: set[str] = set()
    for transcript in transcripts:
        for record in read_records(transcript):
            correlation = record.get("correlation") or {}
            call_id = str(correlation.get("tool_call_id") or "")
            if not call_id:
                continue
            target = synthetic if correlation.get("tool_call_id_synthetic") else native
            target.add(call_id)
    return {"native": len(native), "synthetic": len(synthetic)}


def validation_summary(report: dict[str, Any], replay_files: set[str]) -> dict[str, Any]:
    files = [item for item in report.get("files", []) if item.get("file_path") in replay_files]
    violations = improvements = semconv_issues = signals = samples = 0
    for item in files:
        for signal in item.get("signals", []):
            signals += 1
            for sample in signal.get("samples", []):
                samples += 1
                semconv_issues += len(sample.get("semconv_findings", []))
                for finding in sample.get("policy_findings", []):
                    if finding.get("level") == "violation":
                        violations += 1
                    elif finding.get("level") == "improvement":
                        improvements += 1
    return {
        "total_files": len(files),
        "total_signals": signals,
        "total_samples": samples,
        "violations": violations,
        "improvements": improvements,
        "semconv_issues": semconv_issues,
        "pass": violations == 0 and semconv_issues == 0,
    }


def finalize_manifest(
    golden_dir: Path,
    validation_path: Path,
    plugin_yaml: Path,
    aivision_url: str | None = None,
) -> dict[str, Any]:
    manifest_path = golden_dir / "provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(validation_path.read_text(encoding="utf-8"))
    version = plugin_version(plugin_yaml)
    transcript_dir = golden_dir / "transcripts"
    replay_dir = golden_dir / "replays"
    by_name = {entry["scenario"]: entry for entry in manifest.get("scenarios", [])}

    lifecycle = lifecycle_transcripts(transcript_dir)
    if lifecycle:
        by_name["reset/finalize"] = {
            "scenario": "reset/finalize",
            "status": "passed",
            "command": ["hermes", "--accept-hooks", "--yolo"],
            "interactive_inputs": ["请回复 golden-reset-ready，不要调用工具。", "/new now golden-reset", "/exit"],
            "transcripts": [str(path) for path in lifecycle],
            "probe": "Hermes v0.18.2 interactive /new now emitted real on_session_reset and on_session_finalize hooks.",
        }
    by_name["api-error"] = {
        "scenario": "api-error",
        "status": "skipped",
        "skip_reason": "safe isolated API failure trigger was not authorized; user provider configuration and credentials were not mutated",
        "probe": "Required real scenarios used the configured provider successfully; no isolated throwaway provider endpoint was available.",
        "transcripts": [],
    }
    by_name["compaction"] = {
        "scenario": "compaction",
        "status": "skipped",
        "skip_reason": "Hermes v0.18.2 Python plugin surface has no documented compaction hook",
        "probe": "Pinned observer contract fixture contains 13 hooks and no compaction lifecycle callback; heuristic inference is forbidden.",
        "transcripts": [],
    }

    for name, entry in by_name.items():
        entry["transcripts"] = list(entry.get("transcripts") or [])
        entry.setdefault("command", None)
        transcripts = [Path(value) for value in entry["transcripts"]]
        replays = [path for transcript in transcripts for path in replay_paths(transcript, replay_dir)]
        replay_jsonl = {path for path in replays if path.endswith(".jsonl")}
        entry.update({
            "hermes_version": manifest.get("hermes_version", ""),
            "plugin_version": version,
            "replays": replays,
            "validation": validation_summary(report, replay_jsonl),
            "tool_call_ids": tool_id_counts(transcripts),
            "diagnostic_only": bool(replays) and not replay_jsonl,
        })
        if entry.get("status") == "passed" and not transcripts:
            entry["status"] = "failed"
            entry["failure_reason"] = "passed scenario has no transcript"

    missing = REQUIRED_SCENARIOS - by_name.keys()
    failed = sorted(
        name for name in REQUIRED_SCENARIOS
        if name not in by_name or by_name[name].get("status") != "passed" or not by_name[name]["validation"]["pass"]
    )
    manifest.update({
        "schema_version": 2,
        "plugin_version": version,
        "validation_path": str(validation_path),
        "validation": report.get("summary", {}),
        "tool_call_ids": tool_id_counts(sorted(transcript_dir.glob("session_*.jsonl"))),
        "scenarios": [by_name[name] for name in SCENARIO_ORDER if name in by_name],
        "finalized_at": utc_now(),
        "complete": not missing and not failed and report.get("summary", {}).get("pass") is True,
        "accepted_baselines": [str(path) for path in sorted((golden_dir / "baselines").glob("*.jsonl"))],
    })
    if aivision_url:
        base = aivision_url.rstrip("/")
        with urllib.request.urlopen(f"{base}/api/stats", timeout=10) as response:
            stats = json.loads(response.read())
        with urllib.request.urlopen(f"{base}/api/violations?levels=all&limit=100", timeout=10) as response:
            findings = json.loads(response.read())
        manifest["aivision_report"] = {
            "base_url": base,
            "total_requests": stats.get("total_requests"),
            "forwarded": stats.get("forwarded"),
            "failed": stats.get("failed"),
            "blocked": stats.get("blocked"),
            "violations": stats.get("violations"),
            "improvements": stats.get("improvements"),
            "semconv_issues": stats.get("semconv_issues"),
            "total_recent_findings": findings.get("total_recent_findings"),
            "total_recent_violations": findings.get("total_recent_violations"),
        }
    if missing:
        manifest["missing_required_scenarios"] = sorted(missing)
    if failed:
        manifest["failed_required_scenarios"] = failed
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--plugin-yaml", type=Path, default=Path(__file__).parents[1] / "observ_hermes/plugin.yaml")
    parser.add_argument("--aivision-url")
    args = parser.parse_args(argv)
    try:
        manifest = finalize_manifest(args.golden_dir, args.validation, args.plugin_yaml, args.aivision_url)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Golden finalize failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "complete": manifest["complete"],
        "validation": manifest["validation"],
        "tool_call_ids": manifest["tool_call_ids"],
    }, ensure_ascii=False))
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
