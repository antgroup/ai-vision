"""Opt-in official Hermes/Relay live-provider acceptance; never load observ-hermes.

The target JSON is private: endpoint, agent_uuid and space_key are required.
NVIDIA_API_KEY is read from the environment and used only by the loopback
forwarder. The host receives a disposable local credential. Run --help first.
"""
from __future__ import annotations

import argparse
import base64
import calendar
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[4]
HERMES_COMMIT = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"


def timestamp_nanoseconds(value: int | str) -> int:
    """Compare RFC 3339 readback to OTLP nanoseconds without rounding."""
    if type(value) is int or (isinstance(value, str) and value.lstrip("-").isdigit()):
        return int(value)
    if not isinstance(value, str):
        raise ValueError("Expected integer nanoseconds or an RFC 3339 timestamp")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value
    )
    if not match:
        raise ValueError("Expected integer nanoseconds or an RFC 3339 timestamp")
    seconds, fraction, offset = match.groups()
    stamp = datetime.fromisoformat(seconds + offset.replace("Z", "+00:00"))
    whole_ns = calendar.timegm(stamp.utctimetuple()) * 1_000_000_000
    return whole_ns + int((fraction or "").ljust(9, "0"))


def private_json(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def onboarding_toml(repo, endpoint, target):
    """Use the actual frontend generator; never duplicate the shipping TOML."""
    javascript = r"""
const fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const file=path.join(input.repo,'ai-vision/vfe/src/pages/onboarding/hermesRelayConfiguration.ts');
import(pathToFileURL(file).href).then(({hermesRelayConfiguration})=>{
  const config=hermesRelayConfiguration(input);
  if(!config)process.exit(2);
  process.stdout.write('version = 1\n\n'+config.toml+'\n');
}).catch(()=>process.exit(2));
"""
    value = {"repo": str(repo), "endpoint": endpoint,
             "agentUuid": target["agent_uuid"], "spaceKey": target["space_key"]}
    result = subprocess.run(["node", "--experimental-strip-types", "-e", javascript], input=json.dumps(value),
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("The current frontend onboarding generator failed")
    basic = base64.b64encode((target["agent_uuid"] + ":" + target["space_key"]).encode()).decode()
    if target["space_key"] in result.stdout or basic in result.stdout:
        raise ValueError("Onboarding TOML unexpectedly contains a credential")
    return result.stdout


def isolated_environment(home, config_file, provider_url, basic, source):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT")
           if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), HERMES_HOME=str(home),
               PYTHONUNBUFFERED="1", NO_PROXY="127.0.0.1,localhost",
               no_proxy="127.0.0.1,localhost", OPENAI_API_KEY="local-forwarder-key",
               CUSTOM_API_KEY="local-forwarder-key", OPENAI_BASE_URL=provider_url,
               HERMES_NEMO_RELAY_PLUGINS_TOML=str(config_file),
               AIVISION_HERMES_AUTHORIZATION=basic)
    if source:
        env["PYTHONPATH"] = str(source)
    return env


def host_config(model, provider_url, mode):
    aux = {"provider": "custom", "model": model, "base_url": provider_url,
           "api_key": "local-forwarder-key", "timeout": 90,
           "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {
        "_config_version": 12,
        "model": {"default": model, "provider": "custom", "base_url": provider_url,
                  "max_tokens": 1024},
        "custom_providers": [{"name": "validation", "base_url": provider_url,
                              "api_key": "local-forwarder-key", "model": model,
                              "extra_body": aux["extra_body"]}],
        "plugins": {"enabled": []}, "platform_toolsets": {"acp": ["file"]},
        "gateway": {"telemetry": {"session_segments": {
            "max_turns": 1, "on_compaction": True}}},
        # Test-only thresholds make a short conversation compactable. These
        # values are never written to the user's profile or onboarding guide.
        "compression": {"enabled": mode == "automatic", "in_place": True,
                        "protect_first_n": 0, "protect_last_n": 3,
                        "min_tail_user_messages": 1, "tail_mode": "legacy",
                        "target_ratio": 0.02, "threshold_tokens": 8000},
        "security": {"allow_lazy_installs": False, "tirith_enabled": False},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "agent": {"api_max_retries": 0, "max_turns": 6, "reasoning_effort": "none"},
        "auxiliary": {"compression": aux, "title_generation": aux},
        "display": {"streaming": False, "final_response_markdown": "raw"},
    }


def execute(args, target):
    sys.path.insert(0, str(args.repo / "ai-vision/agent-plugins/tests/native_agent_e2e"))
    import capture
    from drivers.hermes_compression import ACP
    from nvidia_provider import NvidiaProvider, RateBudget

    key = os.environ.get("NVIDIA_API_KEY")
    if not args.live or not key:
        raise ValueError("Execution requires --live and NVIDIA_API_KEY; no fixture fallback is used")
    args.out.mkdir(parents=True, mode=0o700, exist_ok=False)
    home, workspace = args.out / "hermes-home", args.out / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    receipt = secrets.token_hex(12)
    (workspace / "receipt.txt").write_text("receipt: " + receipt + "\n", encoding="utf-8")
    basic = "Basic " + base64.b64encode(
        (target["agent_uuid"] + ":" + target["space_key"]).encode()).decode()
    report = {"provider": "NVIDIA live", "model": args.model, "receipt": receipt,
              "hermes_version": "0.22.5",
              "mode": args.mode, "acp_messages": [], "outputs": []}
    client = collector = provider = env = None
    original_decoder = capture.decode_export

    def record_original(signal, body, content_type, encoding=""):
        decoded = original_decoder(signal, body, content_type, encoding)
        decoded["raw_http_body"] = {"base64": base64.b64encode(body).decode(),
                                    "sha256": hashlib.sha256(body).hexdigest(),
                                    "content_type": content_type, "content_encoding": encoding}
        return decoded

    try:
        collector = capture.Capture(target["endpoint"].rstrip("/") + "/api/public/otel",
                                    {"Authorization": basic}).__enter__()
        provider = NvidiaProvider(key, RateBudget(), proxy=args.proxy,
                                  timeout=args.timeout).__enter__()
        plugins_file = home / "plugins.toml"
        plugins_file.write_text(onboarding_toml(args.repo, collector.url + "/v1/traces", target),
                                encoding="utf-8")
        private_json(home / "config.yaml", host_config(args.model, provider.url, args.mode))
        env = isolated_environment(home, plugins_file, provider.url, basic, args.hermes_source)
        version = subprocess.run([str(args.hermes), "--version"], env=env,
                                 text=True, capture_output=True, timeout=30)
        report["runtime_version_output"] = version.stdout
        if version.returncode or "Hermes Agent v0.22.5" not in version.stdout:
            raise RuntimeError("The actual runtime must report Hermes Agent v0.22.5")
        if HERMES_COMMIT[:12] in version.stdout:
            report["hermes_commit"] = HERMES_COMMIT
        with patch.object(capture, "decode_export", record_original):
            ctx = SimpleNamespace(runtime=(str(args.hermes),), workspace=workspace)
            client = ACP(ctx, env, time.monotonic() + args.timeout, report["acp_messages"])
            report["pid"] = client.process.pid
            client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                          "clientInfo": {"name": "hermes-relay-acceptance", "version": "1"}})
            result, _ = client.request("session/new", {"cwd": str(workspace), "mcpServers": []})
            session = report["session_id"] = result["sessionId"]
            background = "\n".join(
                f"Historical test note {i}: Earlier setup work is complete; retain only that "
                "receipt.txt is the source of the verification receipt and future turns must read it freshly."
                for i in range(110))
            prompts = ["Background records for later context compression:\n" + background +
                       "\n\nACTUAL TASK: Call read_file with path " + str(workspace / "receipt.txt") +
                       ". It contains a fresh random receipt unknown to you. You MUST call the tool "
                       "before replying. Return exactly the line read from the file.",
                       "Call read_file with path " + str(workspace / "receipt.txt") +
                       " again now, then return only its exact receipt line."]
            for prompt in prompts:
                report["outputs"].append(client.prompt(session, prompt))
                if receipt not in report["outputs"][-1]:
                    raise AssertionError("The actual native response did not contain the random file receipt")
            report["context_before"] = client.prompt(session, "/context")
            if args.mode == "manual":
                report["compression_output"] = client.prompt(session, "/compress")
            report["context_after"] = client.prompt(session, "/context")
            report["outputs"].append(client.prompt(session,
                "After context compression, use read_file to read receipt.txt freshly and return only its receipt line."))
            if receipt not in report["outputs"][-1]:
                raise AssertionError("The native response lost the file receipt after compaction")
            report["exit_code"] = client.close()
            report["native_output"] = "".join(client.output)
            client = None
            stopped = subprocess.run([str(args.hermes), "gateway", "stop"], env=env,
                                     text=True, capture_output=True, timeout=45)
            report["gateway_stop"] = {"exit_code": stopped.returncode}
            # Keep the receiver alive through the official batch export delay.
            time.sleep(6)
    except Exception as error:
        # Host error text may include unrelated configuration. Store a bounded
        # category; the private native logs remain available for investigation.
        report["error_category"] = type(error).__name__
    finally:
        if client:
            report["exit_code"] = client.close()
            report["native_output"] = "".join(client.output)
        if env:
            subprocess.run([str(args.hermes), "gateway", "stop"], env=env,
                           capture_output=True, timeout=45)
        if provider:
            provider.__exit__(None, None, None)
            report["requests"] = provider.requests
        if collector:
            collector.__exit__(None, None, None)
            report["exports"] = collector.exports
        report["spans"] = [s for export in report.get("exports", []) for s in export["spans"]]
        private_json(args.out / "report.json", report)
    return report


def verify(report, target, repo):
    sys.path.insert(0, str(repo / "ai-vision/agent-plugins/tests/framework_e2e"))
    from verify_rag import compare_source_fields, http_query
    spans = report.get("spans", [])
    checks = {"native_receipts": len(report.get("outputs", [])) == 3 and all(
        report.get("receipt", "missing") in text for text in report.get("outputs", [])),
        "actual_tools": len([s for s in spans if s["name"] == "read_file"]) >= 3,
        "otlp_admitted": bool(report.get("exports")) and all(
            export.get("admitted") and export.get("forward_status") == 200 for export in report.get("exports", [])),
        "committed_compaction": any(e["name"] == "compaction" and
            e["attributes"].get("nemo_relay.mark.data.outcome") == "committed"
            for s in spans for e in s.get("events", [])),
        "runtime_exited": report.get("exit_code") == 0,
        "gateway_stopped": report.get("gateway_stop", {}).get("exit_code") == 0}
    posts = [q for q in report.get("requests", []) if q.get("method") == "POST"]
    llm_spans = [s for s in spans if s["attributes"].get("openinference.span.kind") == "LLM"]
    wire_usage = Counter((q.get("request", {}).get("model"),
                          (q.get("usage") or {}).get("prompt_tokens"),
                          (q.get("usage") or {}).get("completion_tokens"),
                          (q.get("usage") or {}).get("total_tokens")) for q in posts)
    exported_usage = Counter((s["attributes"].get("llm.model_name"),
                             s["attributes"].get("llm.token_count.prompt"),
                             s["attributes"].get("llm.token_count.completion"),
                             s["attributes"].get("llm.token_count.total")) for s in llm_spans)
    checks["wire_requests_completed"] = bool(posts) and all(q.get("status") == 200 and q.get("completed") for q in posts)
    checks["exported_usage_matches_wire"] = bool(exported_usage) and not (exported_usage - wire_usage)
    results = {"checks": checks, "traces": {}, "metrics": {}}
    if not spans:
        results["scene_verified"] = False
        return results
    low = min(s["start_time_unix_nano"] for s in spans) // 1_000_000 - 1000
    high = max(s["end_time_unix_nano"] for s in spans) // 1_000_000 + 1000
    prefix = "/openapi/v1/spaces/" + target["space_id"] + "/agents/" + target.get("agent_product", "Hermes")
    raw_times = {}
    for export in report["exports"]:
        for resource in export["otlp"].get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    for event in span.get("events", []):
                        if event["name"] in ("compaction", "compaction.attempt"):
                            identity = next(a["value"]["stringValue"] for a in event["attributes"]
                                            if a["key"] == "nemo_relay.mark.uuid")
                            raw_times[identity] = int(event["timeUnixNano"])

    for tid in sorted({s["trace_id"] for s in spans}):
        expected = [s for s in spans if s["trace_id"] == tid]
        status, payload = http_query(target, prefix + "/trace/" + tid + "/detail",
            params={"from": low, "to": high, "detail": "1", "agent_uuid": target["agent_uuid"]})
        actual = payload.get("data", {}).get("spans", [])
        lookup = {s["span_id"]: s for s in actual}
        checks[tid + ".inventory"] = (status == 200 and len(actual) == len(expected)
                                      and set(lookup) == {s["span_id"] for s in expected})
        checks[tid + ".source_fields"] = bool(actual) and all(
            not compare_source_fields(s, lookup.get(s["span_id"], {}), target) for s in expected)
        llms = [s for s in expected if s["attributes"].get("openinference.span.kind") == "LLM"]
        checks[tid + ".oi_usage"] = bool(llms) and all(
            lookup.get(s["span_id"], {}).get("usage", {}).get(key) == s["attributes"].get(attr)
            for s in llms for key, attr in (("input", "llm.token_count.prompt"),
                ("output", "llm.token_count.completion"), ("total", "llm.token_count.total")))
        events = [e for s in expected for e in s.get("events", []) if e["name"] in ("compaction", "compaction.attempt")]
        stored = [e for s in actual for e in s.get("events", []) if e["name"] in ("compaction", "compaction.attempt")]
        checks[tid + ".compaction"] = len(events) == len(stored) and all(
            e["name"] == a["name"] and e["attributes"] == a["attributes"]
            and timestamp_nanoseconds(a["time_unix_nano"]) == raw_times[e["attributes"]["nemo_relay.mark.uuid"]]
            for e, a in zip(events, stored))
        results["traces"][tid] = payload
        status, metrics = http_query(target, prefix + "/traces/metrics", body={
            "from": low, "to": high, "agent_uuid": target["agent_uuid"], "bucket": "minute",
            "groupBy": "model", "filters": [{"field": "trace_id", "op": "in", "value": [tid]}]})
        data = metrics.get("data", {})
        checks[tid + ".metrics"] = (status == 200 and metrics.get("code") == 0
            and data.get("spanCount") == len(expected) and data.get("generationCount") == len(llms)
            and data.get("toolCount") == sum(s["attributes"].get("openinference.span.kind") == "TOOL" for s in expected)
            and all(data.get("usage", {}).get(key) == sum(s["attributes"].get(attr, 0) for s in llms)
                    for key, attr in (("input", "llm.token_count.prompt"),
                        ("output", "llm.token_count.completion"), ("total", "llm.token_count.total"))))
        results["metrics"][tid] = metrics
    results["scene_verified"] = all(checks.values())
    results["complete_provider_coverage"] = False
    results["raw_http_bodies_preserved"] = all("raw_http_body" in e for e in report["exports"])
    results["physical_posts"] = sum(q.get("method") == "POST" for q in report.get("requests", []))
    results["exported_llms"] = sum(s["attributes"].get("openinference.span.kind") == "LLM" for s in spans)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--hermes", type=Path, help="Official v0.22.5 executable")
    parser.add_argument("--hermes-source", type=Path, help="Optional unchanged official checkout for PYTHONPATH")
    parser.add_argument("--target-file", type=Path, required=True, help="Private JSON: endpoint/agent_uuid/space_key")
    parser.add_argument("--out", type=Path, help="Fresh private artifact directory")
    parser.add_argument("--model", help="Explicit currently available NVIDIA model ID")
    parser.add_argument("--proxy", help="Optional credential-free loopback HTTP proxy")
    parser.add_argument("--mode", choices=("automatic", "manual"), default="automatic")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--live", action="store_true", help="Explicitly authorize the live-provider run")
    parser.add_argument("--verify-report", type=Path, help="Read-only API reconciliation; makes no model requests")
    args = parser.parse_args(argv)
    target = json.loads(args.target_file.read_text(encoding="utf-8"))
    if not all(target.get(key) for key in ("endpoint", "agent_uuid", "space_key", "space_id")):
        parser.error("The private target requires endpoint, agent_uuid, space_key and space_id")
    if args.verify_report:
        report = json.loads(args.verify_report.read_text(encoding="utf-8"))
        destination = args.verify_report.parent
    else:
        if not all((args.hermes, args.out, args.model)):
            parser.error("Execution requires --hermes, --out and --model")
        report = execute(args, target)
        destination = args.out
    result = verify(report, target, args.repo)
    output = destination / ("relay-verification-" + secrets.token_hex(4) + ".json")
    private_json(output, result)
    print(json.dumps({"scene_verified": result["scene_verified"],
                      "complete_provider_coverage": result.get("complete_provider_coverage", False),
                      "verification": str(output)}, indent=2))
    return 0 if result["scene_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
