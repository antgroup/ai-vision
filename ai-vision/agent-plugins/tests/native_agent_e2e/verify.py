"""Independent provider/tool/OTLP oracle. No production normalizer is imported."""
from __future__ import annotations

from collections import defaultdict
import json
import math
import re


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def usage(span):
    attributes = span.get("attributes", {})
    result = []
    for field in ("input", "output"):
        missing = attributes.get(f"agentic.usage.{field}_tokens.missing")
        if missing is not None and not (type(missing) is bool or type(missing) is str and missing in ('true','false')):
            return ('invalid','invalid')
        value = attributes.get(f"gen_ai.usage.{field}_tokens")
        if value is None and (missing is True or missing == "true"):
            result.append(None)
            continue
        if not valid_count(value):
            return ("invalid", "invalid")
        result.append(None if missing is True or missing == "true" else value)
    return tuple(result)


def valid_count(value):
    return type(value) is int and value >= 0


def kind(span):
    attributes = span.get("attributes", {})
    explicit = str(attributes.get("gen_ai.span.kind", "")).upper()
    if explicit:
        return explicit
    return {"chat": "LLM", "execute_tool": "TOOL", "invoke_agent": "CHAT"}.get(attributes.get("gen_ai.operation.name"), "SPAN")


def auxiliary(span):
    return span.get("attributes", {}).get("gen_ai.agent.name") in {"title", "summary", "compaction", "compression", "title_generation"}


def chat_owner(span, index):
    """Assign each descendant to its nearest CHAT, never to two nested rounds."""
    trace = span.get("trace_id")
    parent = span.get("parent_span_id")
    seen = {span.get("span_id")}
    while parent and parent not in seen:
        seen.add(parent)
        ancestor = index.get((trace, parent))
        if ancestor is None:
            return None
        if kind(ancestor) == "CHAT":
            return (trace, parent)
        parent = ancestor.get("parent_span_id")
    return None


def receipt_tool_message(messages, call_id, token):
    messages = decoded(messages)
    return isinstance(messages, list) and any(
        isinstance(message, dict) and message.get("role") == "tool"
        and message.get("tool_call_id") == call_id
        and token in str(message.get("content", "")) for message in messages)


def native_tool_output(messages, call_id, app):
    messages = decoded(messages)
    if not isinstance(messages, list):
        return False
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            if any(isinstance(call, dict) and call.get("id") == call_id
                   for call in message.get("tool_calls", [])):
                return True
    return False


def native_answer_output(messages, token, app):
    messages = decoded(messages)
    if not isinstance(messages, list):
        return False
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            if not message.get("tool_calls") and token in str(message.get("content", "")):
                return True
    return False


def turn_chain(root, pairs, tools, index, token, app):
    """Prove one round's raw tool call -> native result -> raw/native answer."""
    identity = (root.get("trace_id"), root.get("span_id"))
    local_pairs = [(position, span, call) for position, (span, call) in enumerate(pairs)
                   if chat_owner(span, index) == identity and not auxiliary(span)
                   and call.get("purpose") != "auxiliary"]
    local_tools = [tool for tool in tools if chat_owner(tool, index) == identity and not auxiliary(tool)]
    for tool in local_tools:
        attributes = tool.get("attributes", {})
        call_id = attributes.get("gen_ai.tool.call.id", attributes.get("agentic.tool.id"))
        if not isinstance(call_id, str) or not call_id or token not in str(attributes.get("gen_ai.tool.call.result", "")):
            continue
        producers = [(position, span) for position, span, call in local_pairs
                     if any(item.get("id") == call_id for item in call.get("response", {}).get("tool_calls", []))
                     and native_tool_output(span.get("attributes", {}).get("gen_ai.output.messages"), call_id, app)]
        # Only this round's last primary call is its answer. An earlier receipt
        # cannot cover a later failed or unrelated final response.
        for position, final_span, final_call in local_pairs[-1:]:
            response = final_call.get("response", {})
            final_attributes = final_span.get("attributes", {})
            if (response.get("tool_calls") or not isinstance(response.get("content"), str)
                    or token not in response["content"]
                    or not receipt_tool_message(final_call.get("request", {}).get("messages"), call_id, token)
                    or not receipt_tool_message(final_attributes.get("gen_ai.input.messages"), call_id, token)
                    or not native_answer_output(final_attributes.get("gen_ai.output.messages"), token, app)):
                continue
            if any(earlier < position and producer.get("start_time_unix_nano", 0) <= final_span.get("start_time_unix_nano", 0)
                   for earlier, producer in producers):
                return True
    return False


def metric_count(metrics, name):
    """Count exact nonnegative integer counter intervals independently of SDKs."""
    grouped = defaultdict(dict)
    for metric in metrics:
        if metric.get("name") != name:
            continue
        point = metric.get("point", {})
        values = [point[key] for key in ("as_int", "asInt", "as_double", "asDouble") if key in point]
        if len(values) != 1 or isinstance(values[0], bool):
            raise ValueError("counter needs exactly one numeric value")
        try:
            if "as_int" in point or "asInt" in point:
                if not re.fullmatch(r"[0-9]+", str(values[0])):
                    raise ValueError("invalid integer counter")
                raw = int(values[0])
            else:
                raw = float(values[0])
                if not math.isfinite(raw) or not raw.is_integer():
                    raise ValueError("invalid floating counter")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("invalid counter value") from error
        if raw < 0:
            raise ValueError("token/request counter must be a nonnegative integer")
        mode = metric.get("temporality")
        mode = {"AGGREGATION_TEMPORALITY_DELTA": 1, "AGGREGATION_TEMPORALITY_CUMULATIVE": 2}.get(mode, mode)
        if isinstance(mode, bool) or mode not in (1, 2) or metric.get("kind", "sum") != "sum":
            raise ValueError("counter must be an explicit delta/cumulative sum")
        start = point.get("start_time_unix_nano", point.get("startTimeUnixNano", 0))
        end = point.get("time_unix_nano", point.get("timeUnixNano", 0))
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid counter interval") from error
        if start < 0 or end < start:
            raise ValueError("invalid counter interval order")
        identity = json.dumps([metric.get("resource"), metric.get("attributes"),
                               metric.get("scope_name"), metric.get("scope_version"),
                               mode, start if mode == 2 else None], sort_keys=True)
        interval = (start, end)
        previous = grouped[identity].get(interval)
        if previous is not None and previous != int(raw):
            raise ValueError("conflicting duplicate counter interval")
        grouped[identity][interval] = int(raw)
    total = 0
    for identity, intervals in grouped.items():
        mode = json.loads(identity)[-2]
        rows = sorted(intervals.items(), key=lambda item: item[0][1])
        values = [value for _, value in rows]
        if mode == 2:
            if any(later < earlier for earlier, later in zip(values, values[1:])):
                raise ValueError("cumulative counter decreased within its start interval")
            total += values[-1]
        else:
            total += sum(values)
    return total


def token_metric_checks(metrics, calls):
    expected = {"input": 0, "output": 0, "read": 0, "write": 0}
    reported = dict.fromkeys(expected, False)
    reported_calls = dict.fromkeys(expected, 0)
    for call in calls:
        source = call.get("usage") or {}
        cache = source.get("prompt_tokens_details") or {}
        fields = {"input": source.get("prompt_tokens"), "output": source.get("completion_tokens"),
                  "read": cache.get("cached_tokens"), "write": cache.get("cache_write_tokens")}
        for field, value in fields.items():
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return False
                reported[field] = True
                reported_calls[field] += 1
                expected[field] += value
        if fields["input"] is not None:
            fresh = fields["input"] - (fields["read"] or 0) - (fields["write"] or 0)
            if fresh < 0:
                return False
            expected["input"] -= (fields["read"] or 0) + (fields["write"] or 0)
    subsets = {
        "input": [m for m in metrics if m.get("name") == "agent.token.usage" and m.get("attributes", {}).get("type", m.get("attributes", {}).get('gen_ai.token.type')) == "input"],
        "output": [m for m in metrics if m.get("name") == "agent.token.usage" and m.get("attributes", {}).get("type", m.get("attributes", {}).get('gen_ai.token.type')) == "output"],
        "read": [m for m in metrics if m.get("name") == "agent.cache.read_tokens"],
        "write": [m for m in metrics if m.get("name") == "agent.cache.write_tokens"],
    }
    if any(m.get("attributes", {}).get("type", m.get("attributes", {}).get('gen_ai.token.type')) not in ("input", "output")
           for m in metrics if m.get("name") == "agent.token.usage"):
        return False
    try:
        for field, rows in subsets.items():
            # Unknown is not a reported zero: even a fabricated zero sample is
            # rejected when this entire source direction/detail was absent.
            if rows and not reported[field]:
                return False
            name = "agent.token.usage" if field in ("input", "output") else "agent.cache." + ("read_tokens" if field == "read" else "write_tokens")
            if name == 'agent.token.usage' and any(row.get('kind') == 'histogram' for row in rows):
                if not all(row.get('kind') == 'histogram' for row in rows):
                    return False
                totals, counts = [], []
                for row in rows:
                    point = row.get('point', {})
                    count = point.get('count')
                    if isinstance(count, bool) or not re.fullmatch(r'[0-9]+', str(count)) or int(count) <= 0:
                        return False
                    # Histogram sums are token totals; counts independently
                    # prove how many actually reported measurements contributed.
                    common = {key: value for key, value in point.items() if key not in ('sum', 'count')}
                    totals.append({**row, 'kind': 'sum', 'point': {**common, 'asDouble': point.get('sum')}})
                    counts.append({**row, 'kind': 'sum', 'point': {**common, 'asInt': count}})
                if metric_count(counts, name) != reported_calls[field] or metric_count(totals, name) != expected[field]:
                    return False
            elif metric_count(rows, name) != expected[field]:
                return False
    except ValueError:
        return False
    return True


def verify_case(*, app, scenario, model, receipt, result, requests, exports, expected_resource=None, mode='fake', expected_turns=2):
    checks = {}
    def check(name, condition):
        checks[name] = bool(condition)
    spans = [span for export in exports for span in export.get("spans", [])]
    logs = [log for export in exports for log in export.get("logs", [])]
    metrics = [metric for export in exports for metric in export.get("metrics", [])]
    calls = [row for row in requests if row.get("response") and row.get("method") != "GET"]
    expected_resource = expected_resource or {}
    dsh = app in {'dsh01', 'dsh02'}
    product = {"pi": "Pi", "hermes": "Hermes", "opencode": "OpenCode",
               'dsh01': 'DeepSeekHarness', 'dsh02': 'DeepSeekHarness', 'kimi': 'KimiCLI'}.get(app)
    def resource_matches(row):
        resource = row.get("resource")
        if not isinstance(resource, dict) or (product and resource.get("ant.agent.product", None if dsh else product) != product):
            return False
        for key, value in expected_resource.items():
            if key in ("ant.username", "ant.uid"):
                # These registered signal identities are explicitly removed
                # from Resource by the production runtimes. Require the actual
                # attributes, and reject conflicting optional Resource copies.
                if row.get("attributes", {}).get(key) != value or (key in resource and resource[key] != value):
                    return False
            elif resource.get(key) != value:
                return False
        return True
    resource_valid = all(resource_matches(row) for row in spans + logs + metrics)
    check("resource.identity", bool(spans and logs and metrics) and resource_valid)
    generations = sorted((span for span in spans if kind(span) == "LLM"), key=lambda span: span.get("start_time_unix_nano", 0))
    if app == "hermes":
        # Current Hermes semantics distinguish absent usage from a reported
        # zero in both traces and API events. Do not let the legacy numeric
        # comparison silently fold contradictory numeric+missing into None.
        api_logs = [row for row in logs if row.get("attributes", {}).get("event.name") == "api_request"]
        check("usage.hermes.missing_contract", all(
            type(marker) is str and marker in ("true", "false")
            and (marker != "true" or f"gen_ai.usage.{direction}_tokens" not in row.get("attributes", {}))
            for row in generations + api_logs for direction in ("input", "output")
            if (marker := row.get("attributes", {}).get(f"agentic.usage.{direction}_tokens.missing")) is not None))
    tools = [span for span in spans if kind(span) == "TOOL"]
    cancelled = scenario == "cancel"
    check("native.completed", (result.get("details", {}).get("cancelled") is True) if cancelled else result.get("exit_code") == 0)
    check("native.session", bool(result.get("session_id")))
    # Catalog traffic is a real physical request, not an LLM generation. It
    # still needs terminal HTTP evidence; absence of a generation response
    # must never hide an unfinished/failed model-discovery request.
    catalog_completed = all(row.get("completed") is True
                            and type(row.get("status")) is int and row["status"] == 200
                            for row in requests if row.get("method") == "GET")
    check("transport.calls", bool(calls) and catalog_completed and not any(row.get("error") for row in requests))
    primary_calls = [call for call in calls if call.get("purpose") != "auxiliary"]
    if app == 'hermes' and scenario == 'multi-turn' and mode == 'fake':
        # The existing multi-turn case deterministically covers Hermes's
        # official continuation, including its native terminal hook. Live
        # providers can choose stop normally and are not required to truncate.
        from fake_provider import HERMES_LENGTH_CONTINUATION_PROMPT, LENGTH_PARTIAL_CONTENT
        length_calls = [call for call in primary_calls if call.get('finish_reason') == 'length']
        expected_finishes = ['tool_calls', 'length', 'stop'] + ['tool_calls', 'stop'] * (expected_turns - 1)
        check('scenario.length_continuation.wire', len(primary_calls) == len(expected_finishes) and len(length_calls) == 1
            and [call.get('finish_reason') for call in primary_calls] == expected_finishes)
        retry_valid = terminal_valid = False
        primary_generations = [span for span in generations if not auxiliary(span)]
        if len(primary_calls) == len(expected_finishes) and len(length_calls) == 1 and len(primary_generations) == len(expected_finishes):
            original, continued = primary_calls[1], primary_calls[2]
            response = original.get('response', {})
            messages = continued.get('request', {}).get('messages', [])
            token = 'receipt: ' + receipt
            retry_valid = (len(messages) >= 2 and messages[-1] == {'role':'user','content':HERMES_LENGTH_CONTINUATION_PROMPT}
                and messages[-2].get('role') == 'assistant' and messages[-2].get('content') == response.get('content') == LENGTH_PARTIAL_CONTENT
                and any(item.get('role') == 'tool' and token in str(item.get('content', '')) for item in messages[:-2])
                and continued.get('response', {}).get('content') == token)
            native = primary_generations[1]
            attrs = native.get('attributes', {})
            output = decoded(attrs.get('gen_ai.output.messages'))
            terminal_valid = (decoded(attrs.get('gen_ai.response.finish_reasons')) == ['length']
                and type(native.get('status', {}).get('code')) is int and native['status']['code'] == 1 and not attrs.get('error.type')
                and isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict)
                and output[0].get('role') == 'assistant' and not output[0].get('tool_calls')
                and output[0].get('content') == response.get('content') == LENGTH_PARTIAL_CONTENT
                and isinstance(response.get('reasoning_content'), str) and bool(response['reasoning_content'])
                and output[0].get('reasoning_content') == response['reasoning_content']
                and type(attrs.get('gen_ai.usage.reasoning.output_tokens')) is int and attrs['gen_ai.usage.reasoning.output_tokens'] == 1
                and native.get('trace_id') == primary_generations[2].get('trace_id')
                and native.get('parent_span_id') == primary_generations[2].get('parent_span_id'))
        check('scenario.length_continuation.host_retry', retry_valid)
        check('scenario.length_continuation.native_terminal', terminal_valid)
    if scenario in {"stream", "cancel"}:
        check("transport.streaming", bool(primary_calls) and all(call.get("request", {}).get("stream") is True for call in primary_calls))
    if scenario == "unknown-usage":
        check("scenario.usage", bool(primary_calls) and all(call.get("usage") is None for call in primary_calls))
    elif scenario == "zero-usage":
        check("scenario.usage", bool(primary_calls) and all(
            all(valid_count((call.get("usage") or {}).get(field)) and call["usage"][field] == 0
                for field in ("prompt_tokens", "completion_tokens", "total_tokens")) for call in primary_calls))
    elif scenario == "cache-usage":
        check("scenario.usage", bool(primary_calls) and all(
            all(valid_count(((call.get("usage") or {}).get(container) or {}).get(field))
                and call["usage"][container][field] > 0
                for container, field in (("prompt_tokens_details", "cached_tokens"),
                                         ("prompt_tokens_details", "cache_write_tokens"),
                                         ("completion_tokens_details", "reasoning_tokens"))) for call in primary_calls))
    check("ingress.admitted", bool(exports) and all(export.get("admitted") is True for export in exports))
    check("signals.three", bool(spans) and bool(logs) and bool(metrics))
    check("calls.inventory", len(generations) == len(calls) > 0)
    observer = 'observ-dsh' if dsh else 'kimihooks' if app == 'kimi' else 'observ-' + app
    check("scope.native", bool(spans) and all(span.get("scope_name") in {observer, "piagent" if app == "pi" else observer} for span in spans))
    identifiers = [(span.get("trace_id"), span.get("span_id")) for span in spans]
    index = {identity: span for identity, span in zip(identifiers, spans)}
    check("spans.unique", bool(identifiers) and len(identifiers) == len(set(identifiers)))
    graph_valid = True
    for span in spans:
        seen = {span.get("span_id")}
        parent = span.get("parent_span_id")
        while parent:
            source = index.get((span.get("trace_id"), parent))
            if source is None or parent in seen:
                graph_valid = False
                break
            seen.add(parent)
            parent = source.get("parent_span_id")
    check("spans.parent_graph", graph_valid)
    check("spans.terminal", bool(spans) and all(span.get("end_time_unix_nano", 0) >= span.get("start_time_unix_nano", 1) > 0 for span in spans))
    check("session.correlation", bool(spans) and all(span.get("attributes", {}).get("gen_ai.conversation.id") == result.get("session_id") for span in spans))
    # OpenCode creates its primary AssistantMessage before the title request;
    # Hermes can finish a deferred title after the main turn has already ended.
    # Match the explicitly observed native auxiliary agent/purpose first; do
    # not pair source values by token equality or unrelated start timestamps.
    ordered_calls = calls
    if app in ("opencode", "hermes") and any(row.get("purpose") == "auxiliary" for row in calls):
        auxiliary_spans = [span for span in generations if auxiliary(span)]
        primary = [span for span in generations if span not in auxiliary_spans]
        aux_calls = [row for row in calls if row.get("purpose") == "auxiliary"]
        main_calls = [row for row in calls if row.get("purpose") != "auxiliary"]
        generations = auxiliary_spans + primary
        ordered_calls = aux_calls + main_calls
        groups_match = len(auxiliary_spans) == len(aux_calls) and len(primary) == len(main_calls)
    else:
        groups_match = True
    expected_usage = [(row.get("usage", {}).get("prompt_tokens"), row.get("usage", {}).get("completion_tokens"))
                      if row.get("usage") is not None else (None, None) for row in ordered_calls]
    reported_values_valid = all(value is None or valid_count(value) for pair in expected_usage for value in pair)
    check("usage.reportedness", groups_match and reported_values_valid and [usage(span) for span in generations] == expected_usage)
    breakdown_valid = len(generations) == len(calls)
    for span, call in zip(generations, ordered_calls):
        source = call.get("usage") or {}
        for container, field, attribute in (
            ("prompt_tokens_details", "cached_tokens", "gen_ai.usage.cache_read.input_tokens"),
            ("prompt_tokens_details", "cache_write_tokens", "gen_ai.usage.cache_creation.input_tokens"),
            ("completion_tokens_details", "reasoning_tokens", "gen_ai.usage.reasoning.output_tokens"),
        ):
            count = (source.get(container) or {}).get(field)
            actual = span.get("attributes", {}).get(attribute)
            if count is None:
                if attribute in span.get("attributes", {}):
                    breakdown_valid = False
            elif not valid_count(count) or not valid_count(actual) or actual != count:
                breakdown_valid = False
    check("usage.breakdown", breakdown_valid)
    check("model.identity", bool(generations) and all(span.get("attributes", {}).get("gen_ai.request.model") == model for span in generations)
          and all(call.get("request", {}).get("model") == model for call in calls))
    try:
        physical_count = metric_count(metrics, "agent.api_request.count")
    except ValueError:
        physical_count = -1
    check("metrics.physical_calls", physical_count == len(calls) > 0)
    check("metrics.tokens", token_metric_checks(metrics, calls))
    events = [log.get("attributes", {}).get("event.name", log.get("body")) for log in logs]
    check("events.api_request", "api_request" in events)
    if dsh:
        from dsh_source import canonical_matches, native_io_matches, canonical_signal_usage_matches, canonical_token_metrics_match, physical_transport_matches
        if mode=='fake': check('dsh.physical_transport',physical_transport_matches(calls,cancelled=cancelled))
        check('dsh.canonical_source', canonical_matches(result, calls, spans, scenario=scenario, receipt=receipt))
        check('dsh.native_io', native_io_matches(result, calls, spans, cancelled=cancelled))
        check('dsh.canonical_signals', canonical_signal_usage_matches(result, spans, logs, cancelled=cancelled))
        check('dsh.canonical_token_metrics', canonical_token_metrics_match(result, metrics))
        api_logs = [log for log, name in zip(logs, events) if name == 'api_request']
        api_ids = [(log.get('trace_id'), log.get('span_id')) for log in api_logs]
        generation_ids = {(span.get('trace_id'), span.get('span_id')) for span in generations}
        check('events.api_request_inventory', len(api_logs) == len(calls) > 0
              and len(api_ids) == len(set(api_ids)) and set(api_ids) == generation_ids)
        wire_by_generation = {(span.get('trace_id'), span.get('span_id')): call
                              for span, call in zip(generations, ordered_calls)}
        check('events.api_request_facts', bool(api_logs) and all(
            identity in wire_by_generation and log.get('attributes', {}).get('gen_ai.request.model') == model
            and usage(log) == ((wire_by_generation[identity].get('usage') or {}).get('prompt_tokens'),
                              (wire_by_generation[identity].get('usage') or {}).get('completion_tokens'))
            for log, identity in zip(api_logs, api_ids)))
    if app == 'opencode' and scenario == 'api-retry' and mode == 'fake':
        primary_pairs = [(span, call) for span, call in zip(generations, ordered_calls)
                         if not auxiliary(span) and call.get('purpose') != 'auxiliary']
        wire_retry = len(primary_calls) == 3 and [call.get('status') for call in primary_calls] == [429, 200, 200]
        if wire_retry:
            failed, recovered = primary_calls[:2]
            wire_retry = (failed.get('completed') is True and failed.get('usage') is None
                and all(failed.get('request', {}).get(field) == recovered.get('request', {}).get(field)
                        for field in ('model', 'messages', 'tools')))
        check('scenario.api_retry.wire', wire_retry)
        native_retry = False
        if len(primary_pairs) == 3:
            failed, recovered = primary_pairs[0][0], primary_pairs[1][0]
            attrs, restored = failed.get('attributes', {}), recovered.get('attributes', {})
            # Bind the retained delta to the original native CHAT user input,
            # then to the independent first provider request. OpenCode may add
            # system reminders; match the complete original user prefix,
            # never arbitrary reminder/context lines or the full system prompt.
            chat_roots = sorted((span for span in spans if kind(span) == 'CHAT' and not auxiliary(span)),
                                key=lambda span: span.get('start_time_unix_nano', 0))
            root = chat_roots[0] if len(chat_roots) == 1 else {}
            initial_input = decoded(root.get('attributes', {}).get('gen_ai.input.messages'))
            original_user = (initial_input[0].get('content') if isinstance(initial_input, list)
                and len(initial_input) == 1 and isinstance(initial_input[0], dict)
                and initial_input[0].get('role') == 'user' else None)
            source_users = [message for message in primary_pairs[0][1].get('request', {}).get('messages', [])
                            if isinstance(message, dict) and message.get('role') == 'user']
            source_content = source_users[0].get('content') if source_users else None
            source_texts = ([source_content] if isinstance(source_content, str) else
                [part.get('text') for part in source_content if isinstance(part, dict)
                 and part.get('type') == 'text' and isinstance(part.get('text'), str)]
                if isinstance(source_content, list) else [])
            retained_input = (isinstance(original_user, str) and bool(original_user.strip())
                and decoded(attrs.get('gen_ai.input.messages')) == initial_input
                and decoded(restored.get('gen_ai.input.messages')) == initial_input
                and chat_owner(failed, index) == (root.get('trace_id'), root.get('span_id'))
                and bool(source_texts) and (original_user == source_texts[0]
                    or ('\n' not in original_user and source_texts[0].startswith(original_user)
                        and re.match(r'^\n+<system-reminder>', source_texts[0][len(original_user):]) is not None)))
            native_retry = (retained_input and attrs.get('error.type') == '429' and failed.get('status', {}).get('code') in (2, 'STATUS_CODE_ERROR')
                and recovered.get('status', {}).get('code') in (1, 'STATUS_CODE_OK') and not restored.get('error.type')
                and bool(attrs.get('gen_ai.response.id')) and attrs.get('gen_ai.response.id') == restored.get('gen_ai.response.id')
                and bool(failed.get('parent_span_id')) and failed.get('trace_id') == recovered.get('trace_id')
                and failed.get('parent_span_id') == recovered.get('parent_span_id')
                and failed.get('end_time_unix_nano', 0) <= recovered.get('start_time_unix_nano', 0))
        check('scenario.api_retry.native_attempts', native_retry)
        api_logs = [log for log in logs if log.get('attributes', {}).get('event.name', log.get('body')) == 'api_request']
        api_ids = [(log.get('trace_id'), log.get('span_id')) for log in api_logs]
        generation_ids = {(span.get('trace_id'), span.get('span_id')) for span in generations}
        failure_logs = [log for log in api_logs if log.get('attributes', {}).get('error.type')]
        check('scenario.api_retry.events', len(api_logs) == len(calls) and set(api_ids) == generation_ids
            and len(failure_logs) == 1 and failure_logs[0].get('attributes', {}).get('error.type') == '429'
            and bool(primary_pairs) and (failure_logs[0].get('trace_id'), failure_logs[0].get('span_id'))
                == (primary_pairs[0][0].get('trace_id'), primary_pairs[0][0].get('span_id')))
        try:
            errors = metric_count(metrics, 'agent.api_request.error.count')
        except ValueError:
            errors = -1
        check('scenario.api_retry.error_metric', errors == 1)
    if not cancelled:
        token = "receipt: " + receipt
        roots = sorted((span for span in spans if kind(span) == "CHAT" and not auxiliary(span)),
                       key=lambda span: span.get("start_time_unix_nano", 0))
        pairs = list(zip(generations, ordered_calls))
        root_ids = {(root.get("trace_id"), root.get("span_id")) for root in roots}
        primary_pairs = [(span, call) for span, call in pairs if not auxiliary(span) and call.get("purpose") != "auxiliary"]
        owned = (len(primary_pairs) == len(primary_calls)
                 and all(chat_owner(span, index) in root_ids for span, _ in primary_pairs)
                 and all(chat_owner(tool, index) in root_ids for tool in tools if not auxiliary(tool)))
        round_checks = [turn_chain(root, pairs, tools, index, token, app) for root in roots]
        check("business.receipt_chain", bool(roots) and graph_valid and owned and all(round_checks))
        check("business.answer", token in result.get("output", ""))
        final_response = primary_calls[-1].get("response", {}) if primary_calls else {}
        check("transport.answer_output", bool(primary_calls)
              and not final_response.get("tool_calls")
              and isinstance(final_response.get("content"), str)
              and token in final_response["content"])
        successful_calls = [call for row in calls for call in row.get("response", {}).get("tool_calls", [])]
        expected_ids = {call["id"] for call in successful_calls}
        actual_ids = [span.get("attributes", {}).get("gen_ai.tool.call.id", span.get("attributes", {}).get("agentic.tool.id")) for span in tools]
        check("tools.call_ids", bool(expected_ids) and len(actual_ids) == len(set(actual_ids)) and set(actual_ids) == expected_ids)
        by_id = {span.get("attributes", {}).get("gen_ai.tool.call.id", span.get("attributes", {}).get("agentic.tool.id")): span for span in tools}
        check("tools.arguments", all(decoded(by_id.get(call["id"], {}).get("attributes", {}).get("gen_ai.tool.call.arguments")) == decoded(call["function"]["arguments"]) for call in successful_calls))
        check("tools.actual_result", any(token in str(span.get("attributes", {}).get("gen_ai.tool.call.result", "")) for span in tools))
        check("transport.tool_result", any(token in json.dumps(row.get("request", {}).get("messages", []), ensure_ascii=False)
                                            and any(message.get("role") == "tool" for message in row.get("request", {}).get("messages", [])) for row in calls))
        check("llm.result_input", any(token in str(span.get("attributes", {}).get("gen_ai.input.messages", "")) for span in generations))
        check("llm.answer_output", any(token in str(span.get("attributes", {}).get("gen_ai.output.messages", "")) for span in generations))
        if scenario == "tool-error":
            check("tools.error_observed", any(span.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR") for span in tools))
    else:
        check("cancel.terminal", any("cancel" in json.dumps(span.get("attributes", {})).lower() or span.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR") for span in generations))
    if scenario == "multi-turn":
        check("session.resumed", result.get("details", {}).get("resumed_session_id") == result.get("session_id"))
        check("turns.multiple", len(roots) == expected_turns)
        for number, passed in enumerate(round_checks):
            check(f"turns.{number}.evidence", passed)
        check("turns.evidence", len(roots) == expected_turns and graph_valid and owned and all(round_checks))
    if app == 'kimi':
        from kimi_source import kimi_matches
        native = kimi_matches(dict(app=app, case=app+'-'+scenario, scenario=scenario,
            version=result.get('version'), mode=mode, model=model, receipt=receipt,
            result=result, requests=requests, exports=exports))
        checks.update(native['checks'])
    failures = [name for name, passed in checks.items() if not passed]
    return {"verified": not failures, "checks": checks, "failures": failures,
            "counts": {"physical_calls": len(calls), "generations": len(generations), "tools": len(tools), "spans": len(spans), "logs": len(logs), "metrics": len(metrics)}}


def acceptance_status(cases, expected):
    names = [case.get("case") for case in cases]
    if not cases or len(names) != len(set(names)) or set(names) != set(expected): return "failed"
    if all(case.get("verified") is True for case in cases): return "passed"
    from source_acceptance import bounded_row
    return "passed_with_known_limitations" if all(case.get("verified") is True or bounded_row(case) for case in cases) else "failed"
