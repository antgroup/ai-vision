#!/usr/bin/env python3
"""Sequential real-session golden collector for Hermes observability."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    mode: str = "oneshot"
    optional_probe: str = ""


SCENARIOS = (
    Scenario("basic-llm", "用一句话介绍你自己，不要调用工具。"),
    Scenario("tool-success", "使用内置文件工具读取当前目录 README.md，并报告行数。", "chat"),
    Scenario("tool-error", "使用内置工具执行 cat /nonexistent/hermes-golden-file-12345，并报告真实错误。", "chat"),
    Scenario("multi-tool", "在 /tmp 创建 hermes-golden.txt，读取它，然后删除它；必须实际调用工具。", "chat"),
    Scenario("subagent", "启动一个子 agent 检查 README.md，同时你报告当前目录。", "chat", "subagent"),
    Scenario("mcp-tool", "使用已配置的 MCP 工具完成一次只读调用。", "chat", "mcp"),
    Scenario("skill-call", "调用一个已安装 skill，并简述执行结果。", "chat", "skill"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def http_json(url: str, *, method: str = "GET") -> dict:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read() or b"{}")


def probe(name: str) -> tuple[bool, str]:
    if not name:
        return True, "required"
    command = {
        "mcp": ["hermes", "mcp", "list"],
        "skill": ["hermes", "skills", "list"],
        "subagent": ["hermes", "tools", "list"],
    }[name]
    result = subprocess.run(command, text=True, capture_output=True)
    evidence = (result.stdout + result.stderr).strip()
    lowered = evidence.casefold()
    if name == "mcp":
        available = result.returncode == 0 and "no mcp servers configured" not in lowered
    elif name == "skill":
        available = result.returncode == 0 and "0 enabled" not in lowered
    else:
        available = result.returncode == 0 and any(
            token in lowered for token in ("delegate", "delegation", "subagent", "spawn")
        )
    return available, evidence[-2000:]


def command_for(scenario: Scenario) -> list[str]:
    if scenario.mode == "oneshot":
        return ["hermes", "--accept-hooks", "--yolo", "-z", scenario.prompt]
    return ["hermes", "--accept-hooks", "--yolo", "chat", "-q", scenario.prompt, "-Q"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", required=True, type=Path)
    parser.add_argument("--aivision-url", default="http://127.0.0.1:4319")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario", action="append", choices=[item.name for item in SCENARIOS])
    parser.add_argument("--append", action="store_true", help="Append selected scenarios to existing provenance")
    args = parser.parse_args(argv)
    if shutil.which("hermes") is None:
        print("golden collection requires the hermes CLI", file=sys.stderr)
        return 2
    version = subprocess.run(["hermes", "--version"], text=True, capture_output=True)
    if version.returncode:
        print(version.stderr, file=sys.stderr)
        return 2
    selected = tuple(item for item in SCENARIOS if not args.scenario or item.name in args.scenario)
    if args.dry_run:
        print(json.dumps({"hermes_version": version.stdout.strip(), "scenarios": [asdict(item) for item in selected]}, ensure_ascii=False, indent=2))
        return 0

    try:
        stats = http_json(f"{args.aivision_url.rstrip('/')}/api/stats")
        if not args.append:
            http_json(f"{args.aivision_url.rstrip('/')}/api/clear", method="POST")
    except Exception as error:
        print(f"AiVision preflight failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    transcript_dir = args.golden_dir / "transcripts"
    replay_dir = args.golden_dir / "replays"
    transcript_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    replay_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = os.environ.copy()
    environment.update({
        "OBSERV_HERMES_DEBUG": "true",
        "OBSERV_HERMES_SESSION_DIR": str(transcript_dir),
        "OBSERV_HERMES_REPLAY_DIR": str(replay_dir),
        "AGENT_ENABLE_TELEMETRY": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": args.aivision_url.rstrip("/"),
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": args.aivision_url.rstrip("/"),
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": args.aivision_url.rstrip("/"),
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": args.aivision_url.rstrip("/"),
    })
    manifest_path = args.golden_dir / "provenance.json"
    if args.append and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prior_names = {entry.get("scenario") for entry in manifest.get("scenarios", [])}
        manifest["scenarios"] = [
            entry for entry in manifest.get("scenarios", [])
            if entry.get("scenario") not in {item.name for item in selected}
        ]
        manifest["replaced_scenarios"] = sorted(prior_names & {item.name for item in selected})
    else:
        manifest = {
            "schema_version": 1,
            "started_at": utc_now(),
            "hermes_version": version.stdout.strip(),
            "aivision_stats": stats,
            "scenarios": [],
        }
    failed = False
    for scenario in selected:
        available, probe_evidence = probe(scenario.optional_probe)
        entry = {
            "scenario": scenario.name,
            "started_at": utc_now(),
            "probe": probe_evidence,
            "command": command_for(scenario),
        }
        if not available:
            entry.update({"status": "skipped", "skip_reason": f"{scenario.optional_probe} surface unavailable"})
            manifest["scenarios"].append(entry)
            continue
        before = {path.name for path in transcript_dir.glob("session_*.jsonl")}
        command = entry["command"]
        result = subprocess.run(command, cwd=Path.cwd(), env=environment, text=True, capture_output=True)
        after = sorted(path for path in transcript_dir.glob("session_*.jsonl") if path.name not in before)
        entry.update({
            "command": command,
            "exit_status": result.returncode,
            "status": "passed" if result.returncode == 0 else "failed",
            "transcripts": [str(path) for path in after],
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
            "finished_at": utc_now(),
        })
        manifest["scenarios"].append(entry)
        if result.returncode != 0 or not after:
            failed = True
    manifest["finished_at"] = utc_now()
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, manifest_path)
    print(f"Golden collection complete -> {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
