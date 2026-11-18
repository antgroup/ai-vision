"""Opt-in Relay 0.10.4 policy regressions; synthetic data, no model or network.

Uses the shipping frontend generator. Run with a Python environment containing
nemo-relay==0.10.4 and Node with --experimental-strip-types support.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.metadata
import json
from pathlib import Path
import tomllib

from relay_native_e2e import REPO, private_json
from relay_redaction_e2e import generated_toml


CASES = {
    "password": ({"password": "synthetic-label-secret", "count": 3}, ["synthetic-label-secret"]),
    "escaped_password": ({"password": 'synthetic-"quoted-secret', "count": 3}, ["quoted-secret"]),
    "cookie_text": ({"text": "Cookie: session=synthetic-cookie-text"}, ["synthetic-cookie-text"]),
    "header_keys": ({"headers": {"Cookie": "synthetic-cookie-value", "Authorization": "synthetic-auth-value"}},
                    ["synthetic-cookie-value", "synthetic-auth-value"]),
    "private": ({"pem": "-----BEGIN PRIVATE KEY-----\nSYNTHETICPRIVATEBODY\n-----END PRIVATE KEY-----"},
                ["SYNTHETICPRIVATEBODY"]),
    "unfinished_private": ({"pem": "-----BEGIN RSA PRIVATE KEY-----\nSYNTHETICUNFINISHEDBODY"},
                           ["SYNTHETICUNFINISHEDBODY"]),
    "bare_label_text": ({"text": "password=synthetic-bare-value"}, ["synthetic-bare-value"]),
    "quoted_label_text": ({"text": 'password="synthetic quoted value"'}, ["synthetic quoted value"]),
    "single_quoted_text": ({"text": "password='synthetic single value'"}, ["synthetic single value"]),
    "authorization_text": ({"text": "Authorization: synthetic-auth-text"}, ["synthetic-auth-text"]),
    "client_secret": ({"client_secret": "synthetic-client-value"}, ["synthetic-client-value"]),
    "provider_prefix": ({"text": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"},
                        ["ghp_abcdefghijklmnopqrstuvwxyz1234567890"]),
}


async def verify_policy(repo: Path):
    import nemo_relay as relay
    from nemo_relay import plugin

    checks = {}
    target = {"agent_uuid": "11111111-1111-4111-8111-111111111111", "space_key": "synthetic-policy-only"}
    configuration = tomllib.loads(generated_toml(
        repo, "http://127.0.0.1:4318/v1/traces", target,
        privacy={"personalInformation": True, "omittedPaths": ["/path"]},
    ))
    # This suite exercises the real canonical sanitizer boundary. The separate
    # native acceptance runner verifies the exporters, wire and platform.
    configuration["components"] = [c for c in configuration["components"] if c["kind"] == "pii_redaction"]
    for name, (value, canaries) in CASES.items():
        events, seen = [], {}
        activation = await plugin.initialize(configuration)
        relay.subscribers.register("policy-regression", lambda event: events.append(event.to_dict()))
        arguments = json.dumps(value)
        call = {"id": "call-policy", "type": "function", "function": {"name": "test", "arguments": arguments}}
        request = {"model": "policy-model", "messages": [{"role": "assistant", "tool_calls": [call]}]}
        response = {"id": "response-policy", "object": "chat.completion", "model": "policy-model",
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant", "tool_calls": [call]}}]}

        async def provider(actual):
            seen["request"] = copy.deepcopy(actual.content)
            return response

        try:
            with relay.scope.scope("policy", relay.ScopeType.Agent):
                result = await relay.llm.execute("llm", relay.LLMRequest({}, request), provider,
                    codec=relay.codecs.OpenAIChatCodec(), response_codec=relay.codecs.OpenAIChatCodec())
            await relay.subscribers.flush_async()
            checks[name + ".execution_unchanged"] = seen.get("request") == request and result == response
            projected = []
            for event in events:
                if event.get("category") != "llm":
                    continue
                data = event["data"]
                message = (data["content"]["messages"][0] if event["scope_category"] == "start"
                           else data["choices"][0]["message"])
                try:
                    projected.append(json.loads(message["tool_calls"][0]["function"]["arguments"]))
                except (ValueError, KeyError):
                    projected.append(None)
            checks[name + ".structured_arguments"] = len(projected) == 2 and all(isinstance(v, dict) for v in projected)
            checks[name + ".canaries_absent"] = all(c not in json.dumps(events) for c in canaries)
            if "count" in value:
                checks[name + ".nonsecret_count"] = all(isinstance(v, dict) and v.get("count") == 3 for v in projected)
        finally:
            relay.subscribers.deregister("policy-regression")
            await activation.close()

    events = []
    activation = await plugin.initialize(configuration)
    relay.subscribers.register("policy-regression", lambda event: events.append(event.to_dict()))
    preserved = {"kind": "committed", "attempt_id": target["agent_uuid"],
                 "session_id": "01234567890123456789012345678901",
                 "sha1": "0123456789abcdef0123456789abcdef01234567",
                 "tokens_before": 4999, "tokens_after": 1611, "reclaimed_tokens": 3388, "duration_ms": 7902}
    raw_tool = {"text": "alex@example.test +1-415-555-0123", "password": "synthetic-opaque-password",
                "headers": {"Authorization": "synthetic-opaque-header"}, "client_secret": "synthetic-opaque-client"}
    observed = {}

    async def tool(actual):
        observed["arguments"] = copy.deepcopy(actual)
        return relay.ToolExecutionResult(raw_tool)

    try:
        with relay.scope.scope("policy", relay.ScopeType.Agent):
            result = await relay.tools.execute("tool", raw_tool, tool)
            for omitted in (1234, False, {"secret": "synthetic-business-value"}, ["synthetic-business-value"]):
                relay.scope.event("compaction", data={**preserved, "path": omitted, "summary": raw_tool["text"]})
        await relay.subscribers.flush_async()
        checks["tool.execution_unchanged"] = observed.get("arguments") == raw_tool and result.result == raw_tool
        encoded = json.dumps(events)
        checks["tool.pii_and_structured_credentials_absent"] = all(
            value not in encoded for value in ("alex@example.test", "+1-415-555-0123", "synthetic-opaque-password",
                "synthetic-opaque-header", "synthetic-opaque-client", "synthetic-business-value"))
        marks = [event["data"] for event in events if event["kind"] == "mark"]
        checks["compaction.identifiers_and_measurements"] = len(marks) == 4 and all(
            all(mark.get(key) == value for key, value in preserved.items()) for mark in marks)
        checks["custom_path.all_json_types_omitted"] = len(marks) == 4 and all("path" not in mark for mark in marks)

        # A compression model can repeat a phone number in reasoning without
        # including it in the visible summary. Keep the original model reply.
        events.clear()
        phone_text = "Earlier phone: +1-415-555-0123"
        reasoning_detail = {"type": "reasoning.text", "id": "reasoning-id", "text": phone_text}
        reasoning = {"reasoning": phone_text, "reasoning_content": phone_text,
                     "reasoning_details": [reasoning_detail]}
        earlier = {"role": "assistant", "content": "Earlier answer: +1-415-555-0123", **reasoning}
        summary = {"role": "assistant", "content": "Summary", **reasoning}
        request = {"model": "policy-model", "messages": [
            earlier, {"role": "user", "content": "Summarize."},
        ]}
        response = {"id": "response-summary", "object": "chat.completion", "model": "policy-model",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": summary}]}

        async def summarize(actual):
            observed["summary_request"] = copy.deepcopy(actual.content)
            return response

        with relay.scope.scope("policy", relay.ScopeType.Agent):
            reply = await relay.llm.execute("llm", relay.LLMRequest({}, request), summarize,
                codec=relay.codecs.OpenAIChatCodec(), response_codec=relay.codecs.OpenAIChatCodec())
        await relay.subscribers.flush_async()
        checks["model_reasoning.execution_unchanged"] = (
            observed.get("summary_request") == request and reply == response)
        projected_input = [
            event["data"]["content"]["messages"][0] for event in events
            if event.get("category") == "llm" and event.get("scope_category") == "start"
        ]
        projected_output = [
            choice["message"] for event in events
            if event.get("category") == "llm" and event.get("scope_category") == "end"
            for choice in event["data"]["choices"]
        ]
        masked_phone = "Earlier phone: [REDACTED]"
        redacted = {"reasoning": masked_phone, "reasoning_content": masked_phone,
                    "reasoning_details": [{**reasoning_detail, "text": masked_phone}]}
        checks["model_reasoning.input_phone_redacted"] = projected_input == [
            {**earlier, **redacted, "content": "Earlier answer: [REDACTED]"},
        ]
        checks["model_reasoning.output_phone_redacted"] = projected_output == [{**summary, **redacted}]
    finally:
        relay.subscribers.deregister("policy-regression")
        await activation.close()
    return {"relay_version": importlib.metadata.version("nemo-relay"), "cases": len(CASES),
            "scope": "canonical Relay events only; no Hermes, OTLP network, database or browser",
            "checks": checks, "passed": all(checks.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify_policy(args.repo))
    if args.report:
        private_json(args.report, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
