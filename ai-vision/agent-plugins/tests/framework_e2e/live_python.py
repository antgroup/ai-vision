"""Explicit, bounded real-provider Python SDK acceptance.

Requires AIVISION_LIVE_PROVIDER_KEY plus an explicit provider URL/model. Records
the provider's original JSON/SSE usage before the OpenAI or LangChain parser.
No credential is written to artifacts. This is opt-in and never part of fake CI.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlsplit
import uuid

import httpx

from execute_rag import clear_environment, targets, versions
from sdk_transport import CaptureForwarder, Ledger, remember_private, safe, write_json
from verify_rag import (compare_native_spans, compare_source_fields, compare_v1_observations,
                        decoded, http_query, iso_nano, observation_type, projected_io,
                        projected_metadata, span_usage)


def provider_usage(body, *, streamed):
    """Parse raw provider bytes independently of both instrumented client SDKs."""
    records = []
    if streamed:
        for line in body.decode("utf-8").splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                records.append(json.loads(line[5:].strip()))
    else:
        records = [json.loads(body)]
    usages = [row["usage"] for row in records if isinstance(row.get("usage"), dict)]
    return {"usage_events": usages, "usage": usages[-1] if usages else None,
            "text": "".join(choice.get("delta" if streamed else "message", {}).get("content") or ""
                            for row in records for choice in row.get("choices", []))}


class ProviderProxy:
    """Only forwards Chat Completions to the explicitly selected provider."""
    def __init__(self, base_url, key):
        self.base_url, self.key, self.records = base_url.rstrip("/"), key, []
        self.client = httpx.Client(timeout=60)
        self.request_lock = threading.Lock()
        self.requested_cases = set()

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = json.loads(body)
                record = {"case": self.headers.get("X-AIV-Test-Case"), "request": request,
                          "started_ns": time.time_ns(), "upstream_attempted": False}
                received = bytearray()
                try:
                    limits = [request[name] for name in ("max_tokens", "max_completion_tokens") if name in request]
                    valid = (len(limits) == 1 and type(limits[0]) is int and limits[0] == 32
                             and request.get("stream") is True
                             and request.get("stream_options", {}).get("include_usage") is True)
                    with owner.request_lock:
                        allowed = (record["case"] in {"langchain-stream", "direct-stream", "provider-error"}
                                   and record["case"] not in owner.requested_cases)
                        if valid and allowed:
                            owner.requested_cases.add(record["case"])
                            record["upstream_attempted"] = True
                    if not valid or not allowed:
                        record.update(status=400 if not valid else 429,
                                      proxy_error="RequestBudgetRejected")
                        self.send_error(record["status"], "Bounded test request rejected")
                        return
                    with owner.client.stream("POST", owner.base_url + "/chat/completions", content=body,
                                             headers={"Authorization": "Bearer " + owner.key,
                                                      "Content-Type": "application/json"}) as response:
                        record["status"] = response.status_code
                        self.send_response(response.status_code)
                        self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        for chunk in response.iter_bytes():
                            received.extend(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except Exception as error:
                    record["proxy_error"] = type(error).__name__
                finally:
                    record["ended_ns"] = time.time_ns()
                    record["response_bytes"] = len(received)
                    try:
                        record.update(provider_usage(bytes(received), streamed=bool(request.get("stream")) and record.get("status") == 200))
                    except (ValueError, UnicodeDecodeError):
                        record["response_parse_error"] = True
                    owner.records.append(safe(record))
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.client.close()


def valid_provider_usage(usage):
    """Validate the provider's final accounting against this runner's budget."""
    return (isinstance(usage, dict)
            and all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("input", "output", "total"))
            and usage["input"] + usage["output"] == usage["total"]
            and usage["output"] <= 32)


def reconcile(cases, records, exports):
    spans = [span for row in exports for span in row.get("spans", [])]
    checks = {}
    for case in cases:
        name = case["case"]
        raw = [record for record in records if record["case"] == name]
        own = [span for span in spans if span["trace_id"] == case["trace_id"]]
        generations = [span for span in own if observation_type(span) == "GENERATION"]
        checks[name + ".single_request"] = len(raw) == 1 and not raw[0].get("proxy_error")
        checks[name + ".single_generation"] = len(generations) == 1
        checks[name + ".unique_spans"] = bool(own) and len({span["span_id"] for span in own}) == len(own)
        if name == "provider-error":
            checks[name + ".actual_error"] = bool(case.get("error")) and bool(raw) and raw[0].get("status") in (400, 404, 422)
            checks[name + ".error_observation"] = bool(generations) and generations[0]["attributes"].get("langfuse.observation.level") == "ERROR"
            checks[name + ".unknown_usage"] = bool(generations) and span_usage(generations[0]) == dict.fromkeys(("input", "output", "total"))
        else:
            checks[name + ".actual_output"] = bool(raw) and raw[0].get("status") == 200 and bool(raw[0].get("text")) and raw[0]["text"] == case.get("output")
            usage = raw[0].get("usage") if raw else None
            expected = {field: usage.get(source) for field, source in
                        (("input", "prompt_tokens"), ("output", "completion_tokens"), ("total", "total_tokens"))} if usage else None
            checks[name + ".provider_usage"] = valid_provider_usage(expected)
            checks[name + ".sdk_usage"] = len(generations) == 1 and expected is not None and span_usage(generations[0]) == expected
    return checks


def recorded_usage(case, records):
    """Independent final provider accounting, including unmeasured errors."""
    raw = [record for record in records if record['case'] == case['case']]
    if len(raw) != 1 or raw[0].get('proxy_error'):
        return None
    if case['case'] == 'provider-error' and raw[0].get('status') in (400, 404, 422) and raw[0].get('usage') is None:
        return dict.fromkeys(('input', 'output', 'total'))
    if raw[0].get('status') == 200 and isinstance(raw[0].get('usage'), dict):
        value = {field: raw[0]['usage'].get(source) for field, source in
                 (('input', 'prompt_tokens'), ('output', 'completion_tokens'), ('total', 'total_tokens'))}
        return value if valid_provider_usage(value) else None
    return None


def verify_native(target, cases, exports, bounds, records):
    checks = {}
    spans = [span for row in exports for span in row.get("spans", [])]
    prefix = f'/openapi/v1/spaces/{target["space_id"]}/agents/{target["agent_product"]}'
    for case in cases:
        expected = [span for span in spans if span["trace_id"] == case["trace_id"]]
        deadline = time.monotonic() + 60
        while True:
            status, body = http_query(target, prefix + "/trace/" + case["trace_id"] + "/detail",
                                      params={**bounds, "agent_uuid": target["agent_uuid"]})
            data = body.get("data", {}) if isinstance(body, dict) else {}
            actual = data.get("spans", []) if isinstance(data, dict) else []
            actual = actual if isinstance(actual, list) and all(isinstance(row, dict) for row in actual) else []
            lookup = {span.get("span_id"): span for span in actual}
            http_ok = (status == 200 and isinstance(body, dict)
                       and type(body.get('code')) is int and body['code'] == 0)
            meta = data.get('meta') if isinstance(data, dict) else None
            complete = isinstance(meta, dict) and meta.get('complete') is True
            identity = bool(actual) and all(not compare_source_fields({}, span, target) for span in actual)
            good = http_ok and complete and identity and not compare_native_spans(expected, actual) and all(
                not compare_source_fields(span, lookup.get(span["span_id"], {}), target) for span in expected)
            if good or status in (400, 401, 403) or time.monotonic() >= deadline:
                break
            time.sleep(1)
        name = case['case'] + '.'
        checks[name + 'native_http'] = http_ok
        checks[name + 'native_complete'] = complete
        checks[name + 'native_identity'] = identity
        checks[name + 'native_readback'] = good
        expected_usage = recorded_usage(case, records)
        semantics = data.get("semantics") if isinstance(data, dict) else None
        usage = semantics.get("usage") if isinstance(semantics, dict) else None
        # Do not derive the expected canonical totals from SDK spans: an SDK or
        # platform aggregation defect could otherwise agree with itself.
        checks[case["case"] + ".native_provider_usage"] = (
            good and expected_usage is not None and isinstance(usage, dict)
            and all(field in usage and type(usage[field]) is type(value) and usage[field] == value
                    for field, value in expected_usage.items()))
    return checks


def source_identity(spans, keys):
    values = {next((span.get('attributes', {}).get(key) for key in keys
                    if span.get('attributes', {}).get(key) is not None), None) for span in spans}
    values.discard(None)
    return next(iter(values)) if len(values) == 1 else None


def same_json(actual, expected):
    """Keep JSON bool, number and string distinct in source projections."""
    return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def source_trace_cost(spans):
    """Use explicit SDK costs only, never a zero inferred from unknown tokens."""
    costs = []
    for span in spans:
        if observation_type(span) not in ('GENERATION', 'EMBEDDING'):
            continue
        raw = decoded(span.get('attributes', {}).get('langfuse.observation.cost_details'))
        if not isinstance(raw, dict):
            return None
        total = raw.get('total')
        if total is None and all(type(raw.get(key)) in (int, float) for key in ('input', 'output')):
            total = raw['input'] + raw['output']
        if type(total) not in (int, float) or not math.isfinite(total) or total < 0:
            return None
        costs.append(total)
    return float(sum(costs)) if costs else None


def v1_trace_matches(expected, actual, target, *, session_row=False):
    """Project original live SDK fields; API responses are never the oracle."""
    roots = [span for span in expected if not span.get('parent_span_id')]
    if len(roots) != 1 or not isinstance(actual, dict):
        return False
    root = roots[0]
    attrs = root.get('attributes', {})
    starts = [span.get('start_time_unix_nano') for span in expected]
    ends = [span.get('end_time_unix_nano') for span in expected]
    if not all(type(value) is int for value in starts + ends):
        return False
    metadata_prefix = ('langfuse.trace.metadata' if any(key == 'langfuse.trace.metadata' or
                       key.startswith('langfuse.trace.metadata.') for key in attrs) else 'langfuse.observation.metadata')
    tags = decoded(attrs.get('langfuse.trace.tags')) or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    fields = {'id': root['trace_id'], 'name': attrs.get('langfuse.trace.name') or root.get('name'),
              'timestamp': iso_nano(min(starts)), 'sessionId': source_identity(expected,
                  ('gen_ai.conversation.id', 'langfuse.session.id', 'session.id')),
              'userId': source_identity(expected, ('langfuse.user.id', 'gen_ai.user.id', 'user.id')),
              'metadata': projected_metadata(attrs, metadata_prefix, root), 'tags': tags,
              'release': attrs.get('langfuse.release'), 'version': attrs.get('langfuse.version'),
              'environment': next((attrs[key] for key in ('langfuse.environment', 'deployment.environment.name',
                                   'deployment.environment') if attrs.get(key) is not None), 'default'), 'public': False}
    for direction in ('input', 'output'):
        key = 'langfuse.trace.' + direction
        fields[direction] = decoded(attrs[key]) if key in attrs else projected_io(root, direction)
    if not session_row:
        fields.update(htmlPath='/project/' + target['agent_uuid'] + '/traces/' + root['trace_id'],
                      latency=(max(ends) - min(starts)) / 1000000000, totalCost=source_trace_cost(expected), scores=[])
    return all(key in actual and same_json(actual[key], value) for key, value in fields.items())


def v1_usage_matches(expected, observations, provider):
    """Check each observation, with generation counts from the provider ledger."""
    if provider is None or not isinstance(observations, list) or not all(isinstance(row, dict) for row in observations):
        return False
    lookup = {row.get('id'): row for row in observations}
    if len(lookup) != len(observations) or set(lookup) != {span['span_id'] for span in expected}:
        return False
    generations = [span for span in expected if observation_type(span) == 'GENERATION']
    if len(generations) != 1 or not same_json(span_usage(generations[0]), provider):
        return False
    for span in expected:
        wanted = provider if observation_type(span) == 'GENERATION' else span_usage(span)
        observed = lookup[span['span_id']]
        legacy = observed.get('usage')
        if (not isinstance(legacy, dict) or legacy.get('unit') != 'TOKENS' or
                any(type(legacy.get(key)) is not int or legacy[key] != (value if value is not None else 0)
                    for key, value in wanted.items())):
            return False
        details = observed.get('usageDetails')
        if all(value is None for value in wanted.values()):
            if details not in (None, {}):
                return False
        elif (not isinstance(details, dict) or any(type(details.get(key)) is not type(value) or details[key] != value
                                                 for key, value in wanted.items() if value is not None)):
            return False
    return True


def verify_v1(target, cases, exports, records):
    """Read-only v1 trace, bounded pagination and source session verification."""
    checks = {}
    spans = [span for row in exports for span in row.get('spans', [])]
    by_trace = {case['trace_id']: [span for span in spans if span['trace_id'] == case['trace_id']] for case in cases}
    for case in cases:
        name, tid = case['case'] + '.', case['trace_id']
        expected = by_trace[tid]
        provider = recorded_usage(case, records)
        status, trace = http_query(target, '/api/public/traces/' + quote(tid, safe=''), basic=True)
        embedded = trace.get('observations', []) if isinstance(trace, dict) else []
        valid_embedded = isinstance(embedded, list) and all(isinstance(row, dict) for row in embedded)
        checks[name + 'v1_trace'] = (status == 200 and v1_trace_matches(expected, trace, target)
                                    and valid_embedded and not compare_v1_observations(expected, embedded))
        observations, complete, total = [], False, None
        # Deliberately use two records per page so the LangChain path exercises
        # pagination. Missing or inconsistent metadata never implies complete.
        for page in range(1, 101):
            status, payload = http_query(target, '/api/public/observations',
                params={'traceId': tid, 'page': page, 'limit': 2}, basic=True)
            rows = payload.get('data') if isinstance(payload, dict) else None
            meta = payload.get('meta') if isinstance(payload, dict) else None
            if (status != 200 or not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
                    or not isinstance(meta, dict) or any(type(meta.get(key)) is not int for key in
                        ('page', 'limit', 'totalItems', 'totalPages')) or meta['page'] != page or meta['limit'] != 2
                    or meta['totalItems'] != len(expected) or meta['totalPages'] != max(1, (len(expected) + 1) // 2)):
                break
            if total is not None and total != meta['totalItems']:
                break
            total = meta['totalItems']
            if len(rows) != min(2, total - len(observations)):
                break
            observations.extend(rows)
            if page == meta['totalPages']:
                complete = len(observations) == total
                break
        checks[name + 'v1_pagination'] = complete
        checks[name + 'v1_observations'] = complete and not compare_v1_observations(expected, observations)
        checks[name + 'v1_provider_usage'] = (complete and v1_usage_matches(expected, embedded, provider)
                                              and v1_usage_matches(expected, observations, provider))
        sid = source_identity(expected, ('gen_ai.conversation.id', 'langfuse.session.id', 'session.id'))
        if not isinstance(sid, str) or not sid:
            for suffix in ('identity', 'inventory', 'traces', 'created'):
                checks[name + 'v1_session_' + suffix] = False
            continue
        own_tids = {trace_id for trace_id, own in by_trace.items() if source_identity(own,
                    ('gen_ai.conversation.id', 'langfuse.session.id', 'session.id')) == sid}
        status, session = http_query(target, '/api/public/sessions/' + quote(sid, safe=''), basic=True)
        session = session if isinstance(session, dict) else {}
        public = session.get('traces', [])
        public = public if isinstance(public, list) and all(isinstance(row, dict) for row in public) else []
        ids = [row.get('id') for row in public]
        checks[name + 'v1_session_identity'] = status == 200 and session.get('id') == sid and session.get('projectId') == target['agent_uuid']
        checks[name + 'v1_session_inventory'] = len(ids) == len(own_tids) and set(ids) == own_tids
        checks[name + 'v1_session_traces'] = bool(public) and all(row.get('id') in own_tids and
            v1_trace_matches(by_trace[row['id']], row, target, session_row=True) for row in public)
        checks[name + 'v1_session_created'] = session.get('createdAt') == iso_nano(min(
            span['start_time_unix_nano'] for trace_id in own_tids for span in by_trace[trace_id]))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--langchain-token-limit-field", choices=("max_completion_tokens", "max_tokens"),
                        default="max_completion_tokens",
                        help="LangChain wire token limit field; max_tokens uses public extra_body without SDK renaming")
    parser.add_argument("--disable-thinking", action="store_true",
                        help="Send chat_template_kwargs.enable_thinking=false via public extra_body for all three cases")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--platform", action="store_true")
    modes.add_argument("--collector-only", action="store_true")
    args = parser.parse_args()
    url = urlsplit(args.provider_base_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("Provider must be an explicit HTTPS origin/API base without credentials")
    key = os.environ.get("AIVISION_LIVE_PROVIDER_KEY")
    if not key:
        parser.error("Set AIVISION_LIVE_PROVIDER_KEY explicitly; no provider config files are read")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("Use a fresh artifact directory")
    args.out.mkdir(parents=True, exist_ok=True)
    target = targets("langfuse", args.platform)
    remember_private(key)
    proxy = ProviderProxy(args.provider_base_url, key)
    clear_environment()
    endpoint = proxy.start()
    capture = CaptureForwarder(target["endpoint"], Ledger(args.out / "ledger.jsonl"))
    from langfuse import Langfuse, propagate_attributes
    from langfuse.langchain import CallbackHandler
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    client = Langfuse(public_key=target["agent_uuid"], secret_key=target["space_key"], base_url=capture.start(), timeout=20)
    report = {"versions": versions(), "provider_base_url": args.provider_base_url, "model": args.model,
              "identity": {"spaceId": target.get("space_id"), "agentUuid": target["agent_uuid"],
                           "agentProduct": target.get("agent_product")},
              "langchain_token_limit_field": args.langchain_token_limit_field, "disable_thinking": args.disable_thinking,
              "mode": "platform" if args.platform else "collector-only", "cases": [], "errors": [],
              "bounds": {"from": time.time_ns() // 1000000}}

    @contextmanager
    def request(name):
        case = {"case": name}
        with propagate_attributes(session_id="live-python-" + uuid.uuid4().hex, user_id="live-provider-test"):
            with client.start_as_current_observation(name=name, as_type="agent", input="Reply with exactly OK.") as observation:
                case["trace_id"] = client.get_current_trace_id()
                try:
                    yield case
                except Exception as error:
                    case["error"] = type(error).__name__
                    observation.update(level="ERROR", status_message=type(error).__name__)
                finally:
                    observation.update(output=case.get("output"))
                    report["cases"].append(case)

    try:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if args.disable_thinking else {}
        # The OpenAI integration instruments the OpenAI module globally. Run the
        # callback-only path first so each request has exactly one integration.
        with request("langchain-stream") as case:
            if args.langchain_token_limit_field == "max_tokens":
                token_limit = {"extra_body": {**extra_body, "max_tokens": 32}}
            else:
                token_limit = {"max_tokens": 32, "extra_body": extra_body}
            model = ChatOpenAI(model=args.model, api_key="loopback-only", base_url=endpoint, **token_limit,
                               max_retries=0, stream_usage=True, use_responses_api=False,
                               default_headers={"X-AIV-Test-Case": "langchain-stream"})
            graph = create_agent(model=model, tools=[])
            async def stream():
                text = []
                async for message, _ in graph.astream({"messages": [{"role": "user", "content": "Reply with exactly OK."}]},
                        config={"callbacks": [CallbackHandler(public_key=target["agent_uuid"])]}, stream_mode="messages"):
                    if isinstance(message.content, str):
                        text.append(message.content)
                return "".join(text)
            case["output"] = asyncio.run(stream())
        from langfuse.openai import openai
        for name in ("direct-stream", "provider-error"):
            with request(name) as case:
                with openai.OpenAI(api_key="loopback-only", base_url=endpoint, max_retries=0,
                                   default_headers={"X-AIV-Test-Case": name}) as provider:
                    chunks = provider.chat.completions.create(
                        model=args.model if name == "direct-stream" else "aivision-invalid-model-" + uuid.uuid4().hex,
                        messages=[{"role": "user", "content": "Reply with exactly OK."}],
                        stream=True, stream_options={"include_usage": True}, max_tokens=32, extra_body=extra_body)
                    with chunks:
                        case["output"] = "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices)
    except BaseException as error:
        report['errors'].append({'stage': 'execution', 'type': type(error).__name__})
    finally:
        # Each owner must get its cleanup attempt, even if a previous owner
        # fails. Exception messages may contain auth material; persist types
        # and stages only, alongside the original captured evidence.
        for stage, close in (('client.flush', client.flush), ('client.shutdown', client.shutdown),
                             ('capture.close', capture.close), ('proxy.close', proxy.close)):
            try:
                close()
            except BaseException as error:
                report['errors'].append({'stage': stage, 'type': type(error).__name__})
        report["bounds"]["to"] = time.time_ns() // 1000000
        for filename, evidence in (('sdk-exports.json', capture.exports), ('provider-ledger.json', proxy.records)):
            try:
                write_json(args.out / filename, evidence)
            except BaseException as error:
                report['errors'].append({'stage': 'write.' + filename, 'type': type(error).__name__})
    report['checks'] = {}
    verifiers = [
        ('verify.source', lambda: reconcile(report['cases'], proxy.records, capture.exports)),
        ('verify.transport', lambda: {'sdk_ingestion_accepted': capture.summary()['ingestion_http_accepted']}),
        ('verify.cases', lambda: {'all_cases_executed': {case['case'] for case in report['cases']} ==
                                {'direct-stream', 'provider-error', 'langchain-stream'}}),
    ]
    if args.platform:
        verifiers.append(('verify.native', lambda: verify_native(
            target, report['cases'], capture.exports, report['bounds'], proxy.records)))
        verifiers.append(('verify.v1', lambda: verify_v1(target, report['cases'], capture.exports, proxy.records)))
    for stage, verify in verifiers:
        try:
            report['checks'].update(verify())
        except BaseException as error:
            report['errors'].append({'stage': stage, 'type': type(error).__name__})
    report["status"] = "passed" if report['checks'] and all(report["checks"].values()) and not report['errors'] else "failed"
    try:
        write_json(args.out / "report.json", report)
    except BaseException as error:
        report['status'] = 'failed'
        report['errors'].append({'stage': 'write.report.json', 'type': type(error).__name__})
    print(json.dumps({"status": report["status"], "mode": report["mode"],
                      "failed_checks": [key for key, value in report["checks"].items() if not value],
                      "errors": report['errors']}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
