"""Opt-in native Hermes privacy acceptance with a deterministic loopback model.

No provider credentials are read. All secret-shaped values are synthetic. The
shipping onboarding generator supplies the Relay configuration; an optional
private target enables real platform ingestion and API reconciliation.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from relay_native_e2e import REPO, host_config, isolated_environment, private_json, timestamp_nanoseconds

CANARIES = {
    "email": "alex@example.test",
    "phone": "+1-415-555-0123",
    "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
    "github": "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    "bearer": "Bearer abcdefghijklmnopqrstuvwxyz012345",
    "basic": "Basic YWNjZXB0YW5jZTpzeW50aGV0aWM=",
    "password": "password=synthetic-password-only-20261103",
    "cookie": "Cookie: session=synthetic-cookie-only-20261103",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljX3NpZ25hdHVyZQ",
    "private_key": "-----BEGIN PRIVATE KEY-----\nU1lOVEhFVElDLU5PVC1BLUtFWQ==\n-----END PRIVATE KEY-----",
}
CANARIES["unterminated_private_key"] = "-----BEGIN RSA PRIVATE KEY-----\nU1lOVEhFVElDLVVOVEVSTUlOQVRFRC1PTkxZ\nstill-private-body"
TEXT = "\n".join(CANARIES.values())


def generated_toml(repo, endpoint, target, *, privacy=None):
    """Load production code and opt into the exact UI privacy options tested."""
    javascript = r"""
const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
import(pathToFileURL(path.join(input.repo,'ai-vision/vfe/src/pages/onboarding/hermesRelayConfiguration.ts')).href)
.then(({hermesRelayConfiguration})=>{
 const value=hermesRelayConfiguration(input); if(!value)process.exit(2);
 process.stdout.write('version = 1\n\n'+value.toml+'\n');
}).catch(()=>process.exit(2));
"""
    value = {"repo": str(repo), "endpoint": endpoint,
             "agentUuid": target["agent_uuid"], "spaceKey": target["space_key"],
             "privacy": privacy if privacy is not None else {"personalInformation": True, "omittedPaths": ["/path"]}}
    result = subprocess.run(["node", "--experimental-strip-types", "-e", javascript],
                            input=json.dumps(value), text=True, capture_output=True, timeout=30)
    if result.returncode or 'kind = "pii_redaction"' not in result.stdout:
        raise RuntimeError("The production onboarding privacy configuration failed")
    return result.stdout


def run(args):
    sys.path.insert(0, str(args.repo / "ai-vision/agent-plugins/tests/native_agent_e2e"))
    import capture
    import fake_provider
    from drivers.hermes_compression import ACP
    from langchain_core.messages import AIMessage

    target = (json.loads(args.target_file.read_text()) if args.target_file else
              {"agent_uuid": "11111111-1111-4111-8111-111111111111",
               "space_key": "synthetic-local-only"})
    args.out.mkdir(parents=True, mode=0o700, exist_ok=False)
    home, workspace = args.out / "hermes-home", args.out / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    # The email is also a real tool argument. Only the native file tool can
    # discover the random receipt; the model transport never reads this file.
    directory = workspace / CANARIES["email"]
    directory.mkdir(mode=0o700)
    receipt_path = directory / "receipt.txt"
    receipt = secrets.token_hex(12)
    receipt_path.write_text("receipt: " + receipt + "\n" + TEXT + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    basic = "Basic " + base64.b64encode(
        (target["agent_uuid"] + ":" + target["space_key"]).encode()).decode()
    report = {"provider": "deterministic loopback HTTP; no external model",
              "scenario": "official native Hermes Relay privacy",
              "privacy": {"personalInformation": True, "omittedPaths": ["/path"]},
              "host_tool_redaction": "disabled only in the isolated test profile",
              "receipt": receipt, "acp_messages": [], "outputs": [],
              "scope": {"native_host": True, "live_model": False,
                        "platform_ingestion": bool(args.target_file)}}
    original_reply = fake_provider.reply_for
    original_decode = capture.decode_export

    def reply(body, path, scenario, number, **kwargs):
        result = original_reply(body, path, scenario, number, **kwargs)
        if result.tool_calls:
            return result
        # Preserve the original receipt oracle; attach test content to real
        # model responses, including auxiliary summary responses.
        return AIMessage(content=result.content + "\n" + TEXT)

    def decode(signal, body, content_type, encoding=""):
        decoded = original_decode(signal, body, content_type, encoding)
        plain = gzip.decompress(body) if encoding.lower() == "gzip" else body
        decoded["wire_sha256"] = hashlib.sha256(body).hexdigest()
        decoded["uncompressed_canary_absence"] = {
            key: all(part.encode() not in plain for part in value.splitlines()) for key, value in CANARIES.items()}
        return decoded

    collector = provider = client = env = None
    try:
        collector = capture.Capture(
            target["endpoint"].rstrip("/") + "/api/public/otel" if args.target_file else None,
            {"Authorization": basic}).__enter__()
        provider = fake_provider.FakeProvider(receipt_path, "multi-turn", request_sized_usage=True)
        provider.__enter__()
        plugins_file = home / "plugins.toml"
        plugins_file.write_text(generated_toml(args.repo, collector.url + "/v1/traces", target))
        plugins_file.chmod(0o600)
        report["generated_toml_sha256"] = hashlib.sha256(plugins_file.read_bytes()).hexdigest()
        config = host_config("native-e2e-chat", provider.url, "automatic")
        config["security"]["redact_secrets"] = False
        private_json(home / "config.yaml", config)
        env = isolated_environment(home, plugins_file, provider.url, basic, args.hermes_source)
        env.update(HERMES_REDACT_SECRETS="false", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file")
        version = subprocess.run([str(args.hermes), "--version"], env=env,
                                 text=True, capture_output=True, timeout=30)
        report["runtime_version_output"] = version.stdout.strip()
        if version.returncode or "Hermes Agent v0.22.5" not in version.stdout:
            raise RuntimeError("The actual runtime must report Hermes Agent v0.22.5")
        with patch.object(fake_provider, "reply_for", reply), patch.object(capture, "decode_export", decode):
            ctx = SimpleNamespace(runtime=(str(args.hermes),), workspace=workspace)
            client = ACP(ctx, env, time.monotonic() + args.timeout, report["acp_messages"])
            client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "hermes-relay-redaction-acceptance", "version": "1"}})
            session, _ = client.request("session/new", {"cwd": str(workspace), "mcpServers": []})
            report["session_id"] = session["sessionId"]
            background = "\n".join(
                f"Historical test note {i}: Earlier setup is complete; retain that receipt.txt is the "
                "source of the receipt and later turns must read it freshly. " + TEXT
                for i in range(110))
            prompts = ["Background for automatic compaction:\n" + background +
                       "\nRead the actual file " + str(receipt_path) + " and report its receipt and content.",
                       "Read " + str(receipt_path) + " again now and report its receipt and content.",
                       "After compaction read " + str(receipt_path) + " freshly and report its receipt and content."]
            for prompt in prompts:
                report["outputs"].append(client.prompt(report["session_id"], prompt))
                if receipt not in report["outputs"][-1]:
                    raise AssertionError("The actual native turn did not use the random file receipt")
            export_deadline = time.monotonic() + args.export_wait
            while time.monotonic() < export_deadline:
                with collector.lock:
                    received = [s for e in collector.exports for s in e.get("spans", [])]
                if sum(s.get("name") == "read_file" for s in received) >= 3:
                    break
                time.sleep(0.2)
            report["exit_code"] = client.close()
            client = None
            stopped = subprocess.run([str(args.hermes), "gateway", "stop"], env=env,
                                     capture_output=True, timeout=45)
            report["gateway_stop_exit_code"] = stopped.returncode
            # Let official batch export complete, retaining the capture until
            # shutdown. This does not claim the final session root is exported.
            time.sleep(6)
    except Exception as error:
        report["error_category"] = type(error).__name__
    finally:
        if client:
            report["exit_code"] = client.close()
        if env:
            subprocess.run([str(args.hermes), "gateway", "stop"], env=env,
                           capture_output=True, timeout=45)
        if provider:
            provider.__exit__(None, None, None)
            report["requests"] = provider.requests
        if collector:
            collector.__exit__(None, None, None)
            report["exports"] = collector.exports
        report["spans"] = [s for export in report.get("exports", []) for s in export.get("spans", [])]
        private_json(args.out / "report.json", report)
    report["checks"] = verify(report, receipt_path)
    if args.target_file:
        report["platform"] = verify_platform(report, target, args.repo)
        report["checks"]["platform_readback"] = report["platform"]["passed"]
    report["complete_provider_coverage"] = False
    report["usage_coverage"] = usage_coverage(report)
    report["passed"] = all(report["checks"].values())
    private_json(args.out / "verification.json", report)
    return report



def usage_coverage(report):
    llms = [s for s in report.get("spans", [])
            if s.get("attributes", {}).get("openinference.span.kind") == "LLM"]
    return {"physical_requests": len(report.get("requests", [])),
            "exported_llm_calls": len(llms),
            "exported_calls_with_full_usage": sum(all(s["attributes"].get(key) is not None for key in
                ("llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total")) for s in llms)}


def verify(report, receipt_path):
    exports, requests = report.get("exports", []), report.get("requests", [])
    spans = report.get("spans", [])
    wire = json.dumps(exports, ensure_ascii=False)
    provider_requests = json.dumps([r.get("request") for r in requests], ensure_ascii=False)
    actual_tools = [message for row in requests for message in row.get("request", {}).get("messages", [])
                    if message.get("role") == "tool"]
    tool_wire = "\n".join(str(message.get("content", "")) for message in actual_tools)
    outputs = "\n".join(report.get("outputs", []))
    checks = {"native_turns_and_tools": len(report.get("outputs", [])) == 3 and
              len([s for s in spans if s.get("name") == "read_file"]) >= 3,
              "provider_received_original_tool_path": str(receipt_path) in provider_requests,
              "provider_received_actual_tool_receipt": report["receipt"] in tool_wire,
              "runtime_exited": report.get("exit_code") == 0,
              "gateway_stopped": report.get("gateway_stop_exit_code") == 0,
              "otlp_admitted": bool(exports) and all(e.get("admitted") for e in exports),
              "selected_tool_path_omitted": all('"path"' not in str(s.get("attributes", {}).get("input.value", ""))
                  for s in spans if s.get("name") == "read_file")}
    for key, value in CANARIES.items():
        checks[key + ".original_provider_input"] = all(part in provider_requests for part in value.splitlines())
        checks[key + ".original_tool_result"] = all(part in tool_wire for part in value.splitlines())
        checks[key + ".original_model_output"] = value in outputs
        checks[key + ".decoded_otlp_absent"] = all(part not in wire for part in value.splitlines())
        checks[key + ".raw_otlp_absent"] = bool(exports) and all(
            e.get("uncompressed_canary_absence", {}).get(key) for e in exports)
    committed = [e for s in spans for e in s.get("events", []) if e.get("name") == "compaction" and
                 e.get("attributes", {}).get("nemo_relay.mark.data.outcome") == "committed"]
    checks["native_compaction_preserved"] = bool(committed) and all(
        e["attributes"].get("nemo_relay.mark.uuid") and
        e["attributes"].get("nemo_relay.mark.data.attempt_id") and
        isinstance(e["attributes"].get("nemo_relay.mark.data.tokens_before"), (int, float))
        for e in committed)
    exported_usage = Counter((s["attributes"].get("llm.model_name"),
        s["attributes"].get("llm.token_count.prompt"), s["attributes"].get("llm.token_count.completion"),
        s["attributes"].get("llm.token_count.total")) for s in spans
        if s.get("attributes", {}).get("openinference.span.kind") == "LLM"
        and all(s["attributes"].get(key) is not None for key in
                ("llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total")))
    provider_usage = Counter((r.get("request", {}).get("model"),
        (r.get("usage") or {}).get("prompt_tokens"), (r.get("usage") or {}).get("completion_tokens"),
        (r.get("usage") or {}).get("total_tokens")) for r in requests)
    checks["reported_export_usage_matches_provider"] = bool(exported_usage) and not (exported_usage - provider_usage)
    checks["provider_requests_completed"] = bool(requests) and all(r.get("status") == 200 for r in requests)
    chunks = [e for s in spans for e in s.get("events", []) if e.get("name") == "llm.chunk"]
    checks["stream_marks_contain_only_metadata"] = bool(chunks) and all(
        set(e.get("attributes", {})) <= {
            "nemo_relay.mark.uuid", "nemo_relay.mark.parent_uuid", "nemo_relay.mark.data.choice_indices",
            "nemo_relay.mark.data.chunk_index", "nemo_relay.mark.data.event_type",
            "nemo_relay.mark.data.provider", "nemo_relay.mark.data.finish_reasons", "nemo_relay.mark.data.usage"}
        for e in chunks)
    return checks


def verify_platform(report, target, repo):
    sys.path.insert(0, str(repo / "ai-vision/agent-plugins/tests/framework_e2e"))
    from verify_rag import compare_source_fields, http_query
    sys.path.insert(0, str(repo / "ai-vision/agent-plugins/tests/native_agent_e2e"))
    from capture import attrs

    expected_events = {}
    for export in report.get("exports", []):
        for resource in export.get("otlp", {}).get("resourceSpans", []):
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    identity = base64.b64decode(span["spanId"]).hex()
                    expected_events[identity] = [(e.get("name"), attrs(e.get("attributes", [])),
                        int(e.get("timeUnixNano", 0))) for e in span.get("events", [])]
    spans = report["spans"]
    if not spans:
        return {"checks": {"exported_spans": False}, "passed": False, "traces": {}}
    low = min(s["start_time_unix_nano"] for s in spans) // 1_000_000 - 1000
    high = max(s["end_time_unix_nano"] for s in spans) // 1_000_000 + 1000
    prefix = "/openapi/v1/spaces/" + target["space_id"] + "/agents/Hermes"
    checks, traces = {}, {}
    for trace_id in sorted({s["trace_id"] for s in spans}):
        expected = [s for s in spans if s["trace_id"] == trace_id]
        status, payload = http_query(target, prefix + "/trace/" + trace_id + "/detail",
            params={"from": low, "to": high, "detail": "1", "agent_uuid": target["agent_uuid"]})
        actual = payload.get("data", {}).get("spans", [])
        lookup = {s["span_id"]: s for s in actual}
        checks[trace_id + ".inventory"] = status == 200 and len(actual) == len(expected) and set(lookup) == {s["span_id"] for s in expected}
        checks[trace_id + ".identity_timing_status"] = bool(actual) and all(
            all(lookup.get(s["span_id"], {}).get(key) == s.get(key) for key in ("trace_id", "parent_span_id", "name"))
            and all(timestamp_nanoseconds(lookup[s["span_id"]][key]) == s[key]
                    for key in ("start_time_unix_nano", "end_time_unix_nano"))
            and lookup[s["span_id"]].get("status", {}).get("code", "UNSET") == s.get("status", {}).get("code", "STATUS_CODE_UNSET").replace("STATUS_CODE_", "")
            for s in expected if s["span_id"] in lookup)
        checks[trace_id + ".original_events"] = bool(actual) and all(
            [(e.get("name"), e.get("attributes", {}), timestamp_nanoseconds(e.get("time_unix_nano", 0)))
             for e in s.get("events", [])]
            == expected_events.get(s["span_id"], []) for s in actual)
        checks[trace_id + ".source_fields"] = bool(actual) and all(
            not compare_source_fields(s, lookup.get(s["span_id"], {}), target) for s in expected)
        checks[trace_id + ".canaries_absent"] = all(part not in json.dumps(payload) for value in CANARIES.values() for part in value.splitlines())
        traces[trace_id] = payload
    return {"checks": checks, "passed": bool(checks) and all(checks.values()), "traces": traces}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--hermes", type=Path, required=True)
    parser.add_argument("--hermes-source", type=Path)
    parser.add_argument("--target-file", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="New private evidence directory")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--export-wait", type=int, default=30, help="Bounded wait for the native exporter before gateway shutdown")
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"],
                      "span_count": len(report["spans"]), "report": str(args.out / "verification.json")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
