"""Predefined native expectations, independent of diagnostic API outcomes."""
import json
import hashlib
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit


def validate_targets(value, apps):
    if not isinstance(value, dict) or any(app not in value for app in apps):
        raise ValueError("Every selected native Agent requires a private local target")
    for app in apps:
        target = value[app]
        if target.get("data_backend") != "oceanbase":
            raise ValueError("Diagnostic E2E requires the actual OceanBase platform")
        for field in ("endpoint", "collector_endpoint"):
            parsed = urlsplit(target.get(field, ""))
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("All platform targets must be credential-free HTTP loopback URLs")
        product = "Codex" if app == "codex" else "DeepSeekHarness"
        if target.get("agent_product") != product or any(not target.get(k) for k in (
            "space_id", "space_key", "agent_uuid", "collector_headers", "dashboard_headers")):
            raise ValueError("Incomplete target or mismatched native Agent identity")
    return value


def expectations(app, scenario, *, mode='fake'):
    rules = dict(scenario.expected_rules)
    skips = dict(scenario.expected_skips)
    if app.startswith("dsh") and not any(step["op"] == "shell" for step in scenario.tool_steps):
        skips["high_risk_command"] = "not_applicable"
    if app == "codex" and scenario.name == "sensitive-read":
        rules["high_risk_command"] = "bad"
    return ({} if mode == 'nvidia' else {key: verdict for key, verdict in rules.items() if key not in skips}), skips


def rule_evidence_expectations(scenario):
    """Expected facts derive from executable fixture actions, never API results."""
    if scenario.name in {"cancel", "privacy"}:
        return {}
    evidence = {"tool_success": {"counts": {
        "success": sum(step.get("success") is True for step in scenario.tool_steps),
        "failure": sum(step.get("success") is False for step in scenario.tool_steps),
        "unknown": sum(step.get("success") is None for step in scenario.tool_steps)}}}
    if scenario.name == "failed-verification":
        evidence["test_execution"] = {"verification_result": "failed"}
    elif scenario.name in {"code-change-untested", "incomplete-repair", "misreported-verification"}:
        evidence["test_execution"] = {"verification_result": "missing"}
    elif scenario.name in {"code-change-tested", "repair-with-verification", "rewrite-overlap"}:
        evidence["test_execution"] = {"verification_result": "passed"}
    return evidence


def _json_value(value):
    """Match Coding projection's JSON decoding; preserve opaque tool output."""
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def _assistant_text(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value if value.strip() else None
    if isinstance(value, dict):
        return value.get("content") if value.get("role") == "assistant" else None
    if isinstance(value, list):
        texts = [item["content"] for item in value if isinstance(item, dict)
                 and item.get("role") == "assistant" and isinstance(item.get("content"), str)
                 and item["content"].strip()]
        return "\n".join(texts) if texts else None
    return None


def _native_final(report):
    details = report.get("result", {}).get("details", {})
    if report["app"] == "codex":
        finals = [row.get("payload", {}).get("last_agent_message")
                  for row in details.get("native_transcript", [])
                  if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "task_complete"]
    else:
        finals = []
        for event in details.get("canonical_events", []):
            if event.get("type") != "assistant/message":
                continue
            blocks = event.get("data", {}).get("message", {}).get("content", [])
            texts = [block["text"] for block in blocks if block.get("type") == "text"
                     and isinstance(block.get("text"), str)]
            # A later tool-only message cannot promote earlier text to a final.
            finals.append("\n".join(texts) if texts else None)
    return finals[-1] if finals and isinstance(finals[-1], str) and finals[-1] else None


def _document_read_command(command, document_path):
    """Recognize one bounded read/search invocation, never a shell program."""
    # shlex tokenization cannot model shell expansion; this finite proof
    # contract conservatively rejects those characters even inside quotes.
    if any(value in command for value in ('\n', '\r', '\x00', '$', '`', '{', '}', '*', '?', '[', ']', '~', '(', ')')):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='|&;<>')
        lexer.whitespace_split, lexer.commenters = True, ''
        tokens = list(lexer)
    except ValueError:
        return None
    if (len(tokens) < 2 or not document_path(tokens[-1])
            or any(token and all(char in '|&;<>' for char in token) for token in tokens)):
        return None
    program, options = tokens[0], tokens[1:-1]
    if program == 'cat':
        return ('cat', None) if options in ([], ['--']) else None
    if program in {'head', 'tail'}:
        if options and options[-1] == '--':
            options = options[:-1]
        count = '10'
        if len(options) == 2 and options[0] in {'-n', '--lines'}:
            count = options[1]
        elif len(options) == 1:
            match = re.fullmatch(r'(?:-n|--lines=|-)([0-9]+)', options[0])
            if not match:
                return None
            count = match[1]
        elif options:
            return None
        return (program, int(count)) if re.fullmatch(r'[1-9][0-9]{0,5}', count) else None
    if program != 'rg':
        return None
    numbered, pattern, index = False, None, 0
    while index < len(options):
        option = options[index]
        if option == '--':
            if pattern is not None or index + 2 != len(options):
                return None
            pattern = options[index + 1]
            break
        if option in {'-n', '-ni', '-in', '--line-number'}:
            numbered = True
        elif option in {'-i', '--ignore-case', '--no-heading', '--color=never'}:
            pass
        elif option in {'-C', '--context'}:
            index += 1
            if index >= len(options) or not re.fullmatch(r'[0-9]{1,6}', options[index]):
                return None
        elif re.fullmatch(r'(?:-C|--context=)[0-9]{1,6}', option):
            pass
        elif option.startswith('-') or pattern is not None:
            return None
        else:
            pattern = option
        index += 1
    return ('rg', None) if numbered and pattern else None


def _shell_read_output(result):
    """Unwrap only the known successful Codex shell envelope for comparison."""
    match = re.fullmatch(
        r'Chunk ID: [A-Za-z0-9_-]+\n(?:Wall time: [0-9]+(?:\.[0-9]+)? seconds\n)?'
        r'Process exited with code 0\n(?:Original token count: [0-9]+\n)?'
        r'(?:Final output|Output):\n(?P<body>[\s\S]*)', result)
    return match['body'] if match else result


def _numbered_document_excerpt(lines, document, *, renderer=False):
    original, previous, nonempty = document.splitlines(), 0, False
    separator = r': ' if renderer else r'[:-]'
    for index, line in enumerate(lines):
        if not renderer and line == '--':
            if index == 0 or index == len(lines) - 1 or lines[index - 1] == '--':
                return False
            continue
        match = re.fullmatch(r'([1-9][0-9]{0,5})' + separator + r'(.*)', line)
        if not match:
            return False
        number = int(match[1])
        if number <= previous or number > len(original) or match[2] != original[number - 1]:
            return False
        previous, nonempty = number, nonempty or bool(match[2].strip())
    return nonempty


def _document_read_result(result, document, document_path, *, shell=None):
    """Validate exact document lines while leaving original telemetry untouched."""
    if shell is not None:
        program, count = shell
        output = _shell_read_output(result)
        if program == 'rg':
            return _numbered_document_excerpt(output.splitlines(), document)
        lines = document.splitlines(keepends=True)
        expected = ''.join(lines[:count] if program == 'head' else lines[-count:]) if count else document
        return output == expected and bool(output.strip())
    if result == document:
        return bool(document.strip())
    rendered = re.fullmatch(
        r'<path>(?P<path>[^\n<>]+)</path>\n<type>file</type>\n<content>\n'
        r'(?P<body>[\s\S]*?)\n\n\(End of file - total (?P<total>[1-9][0-9]{0,5}) lines\)\n</content>', result)
    return bool(rendered and document_path(rendered['path'])
        and int(rendered['total']) == len(document.splitlines())
        and _numbered_document_excerpt(rendered['body'].splitlines(), document, renderer=True))


def rag_material_proof(report, scenario, *, workspace=None, projection=None):
    """Prove actual reference delivery, never the natural answer's correctness."""
    document = scenario.files['retrieved.txt']
    digest = hashlib.sha256(document.encode()).hexdigest()
    checks = {'rag.document': bool(document)}
    if workspace is not None:
        path = Path(workspace) / 'retrieved.txt'
        checks['rag.workspace_hash'] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    spans = [span for export in report.get('exports', []) if export.get('admitted') is True
             for span in export.get('spans', [])]
    final = _native_final(report)
    supplied = []
    def document_path(value):
        if not isinstance(value, str):
            return False
        candidate = Path(value)
        if candidate.is_absolute():
            return workspace is not None and candidate == Path(workspace) / 'retrieved.txt'
        return candidate.parts in {('retrieved.txt',), ('.','retrieved.txt')}
    for tool in spans:
        attrs = tool.get('attributes', {})
        if attrs.get('gen_ai.operation.name') != 'execute_tool':
            continue
        args = _json_value(attrs.get('gen_ai.tool.call.arguments'))
        if not isinstance(args, dict):
            continue
        name = str(attrs.get('gen_ai.tool.name', '')).removeprefix('tool ')
        path = args.get('file_path') or args.get('path')
        read = name in {'read', 'Read', 'read_file', 'fs_read_file'} and document_path(path)
        shell = None
        command = args.get('command') or args.get('cmd')
        if name in {'bash', 'Bash', 'exec_command'} and isinstance(command, str):
            shell = _document_read_command(command, document_path)
            read = shell is not None
        result, call = attrs.get('gen_ai.tool.call.result'), attrs.get('gen_ai.tool.call.id')
        if (not read or not call or not isinstance(result, str)
                or not _document_read_result(result, document, document_path, shell=shell)):
            continue
        for llm in spans:
            material = llm.get('attributes', {})
            if material.get('gen_ai.span.kind') != 'LLM' or not final or _assistant_text(material.get('gen_ai.output.messages')) != final:
                continue
            messages = _json_value(material.get('gen_ai.input.messages'))
            if isinstance(messages, dict):
                messages = messages.get('messages')
            if not isinstance(messages, list):
                continue
            for index, message in enumerate(messages):
                if not isinstance(message, dict) or message.get('role') != 'tool' or message.get('tool_call_id') != call or message.get('content') != result:
                    continue
                try:
                    before = int(tool['end_time_unix_nano']) <= int(llm['start_time_unix_nano'])
                except (KeyError, ValueError, TypeError):
                    before = False
                wire = any(request.get('completed') is True and request.get('response', {}).get('content') == final
                    and any(item.get('role') == 'tool' and item.get('tool_call_id') == call and item.get('content') == result
                            for item in request.get('request', {}).get('messages', []) if isinstance(item, dict))
                    for request in report.get('requests', []))
                if before and wire:
                    supplied.append((tool, llm, result, call, index, isinstance(_json_value(material.get('gen_ai.input.messages')), dict)))
    checks['rag.causal_supplied_material'] = bool(supplied)
    if projection is not None:
        contexts = projection.get('provided_context') or []
        def matches(item, proof):
            tool, llm, result, call, index, nested = proof
            location = 'attributes.gen_ai.input.messages' + ('.messages' if nested else '') + f'[{index}].content'
            producer = item.get('producer_source') or {}
            return (item.get('kind') == 'tool_result' and item.get('content') == result
                and any(ref.get('trace_id') == llm['trace_id'] and ref.get('span_id') == llm['span_id']
                    and ref.get('call_id') == call and ref.get('field_path') == location for ref in item.get('sources', []))
                and producer.get('span_id') == tool['span_id'] and producer.get('trace_id') == tool['trace_id']
                and producer.get('call_id') == call and producer.get('field_path') == 'attributes.gen_ai.tool.call.result')
        checks['rag.platform_context'] = any(matches(item, proof) for item in contexts for proof in supplied)
    return {'verified': all(checks.values()), 'checks': checks,
            'document_sha256': digest, 'failures': [key for key, passed in checks.items() if not passed]}


def validate_projection(projection, report, scenario, *, original_facts=None):
    checks = {}
    checks["projection.semantic"] = projection.get("projection_kind") == "semantic"
    checks["projection.identity"] = projection.get("trace_id") in report["source"]["source_trace_ids"]
    checks["projection.runtime"] = projection.get("runtime_type") == ("codex" if report["app"] == "codex" else "dsh")
    private = scenario.name == "privacy"
    canceled = scenario.name == "cancel"
    if not private:
        checks["projection.prompt"] = projection.get("user_query") == scenario.prompt
    spans = {s["span_id"]: s for e in report.get("exports", []) if e.get("admitted") is True
             for s in e.get("spans", []) if s.get("trace_id") == projection.get("trace_id")
             and s.get("span_id") in report["source"]["source_span_ids"]}
    checks["projection.original_spans"] = bool(spans)
    if not private and not canceled:
        final = _native_final(report)
        output_span = spans.get(projection.get("agent_output_span_id"), {})
        output_attrs = output_span.get("attributes", {})
        checks["projection.native_final"] = final is not None
        checks["projection.final_answer"] = final is not None and projection.get("agent_output") == final
        checks["projection.final_source"] = (output_attrs.get("gen_ai.span.kind") in {"CHAT", "LLM"}
            and _assistant_text(output_attrs.get("gen_ai.output.messages")) == projection.get("agent_output")
            and bool(projection.get("agent_output")))
    raw_tools = {key: span for key, span in spans.items()
                 if span.get("attributes", {}).get("gen_ai.operation.name") == "execute_tool"
                 and span.get("attributes", {}).get("gen_ai.span.kind", "TOOL") == "TOOL"}
    for field in ("tool_actions", "file_mutations", "verification_actions"):
        values = projection.get(field)
        checks[field + ":source"] = isinstance(values, list) and all(
            isinstance(item, dict) and item.get("source_span_id") in raw_tools for item in values)
    tools = projection.get("tool_actions") or []
    tool_refs = [item.get("source_span_id") for item in tools if isinstance(item, dict)]
    checks["projection.original_tool_coverage"] = (len(tool_refs) == len(tools) == len(raw_tools)
        and set(tool_refs) == set(raw_tools))
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            checks[f"projection.tool.{index}:fields"] = False
            continue
        attrs = raw_tools.get(tool.get("source_span_id"), {}).get("attributes", {})
        raw_call_id = attrs.get("gen_ai.tool.call.id")
        call_id = str(raw_call_id) if raw_call_id not in (None, "") else None
        checks[f"projection.tool.{index}:original_call_id"] = call_id is not None
        if not private and not canceled:
            for field in ("arguments", "result"):
                checks[f"projection.tool.{index}:original_{field}"] = attrs.get("gen_ai.tool.call." + field) is not None
        raw_name = attrs.get("gen_ai.tool.name") or str(raw_tools.get(tool.get("source_span_id"), {}).get("name") or "")
        name = raw_name[5:] if raw_name.startswith("tool ") else raw_name
        expected = {"call_id": call_id, "action_id": call_id or tool.get("source_span_id"),
            "name": name or None, "operation_name": "execute_tool",
            "arguments": _json_value(attrs.get("gen_ai.tool.call.arguments")),
            "result": _json_value(attrs.get("gen_ai.tool.call.result"))}
        for field, value in expected.items():
            # Compare decoded JSON with its types intact (True must not equal 1).
            checks[f"projection.tool.{index}:{field}"] = field in tool and json.dumps(
                tool[field], sort_keys=True, ensure_ascii=False) == json.dumps(value, sort_keys=True, ensure_ascii=False)
    if report.get("mode", "fake") == "fake" and not canceled:
        checks["projection.tool_coverage"] = len(tools) == len(scenario.tool_steps)
    if original_facts is not None:
        # Replay compares observed original-source facts, never a fake tool plan.
        for field in ("user_query_span_id", "agent_output_span_id", "file_mutations", "verification_actions"):
            checks["projection.original_" + field] = field in projection and field in original_facts and json.dumps(
                projection[field], sort_keys=True, ensure_ascii=False) == json.dumps(
                original_facts[field], sort_keys=True, ensure_ascii=False)
    elif not private and not canceled:
        edits = [step for step in scenario.tool_steps if step["op"] == "edit"]
        mutations = projection.get("file_mutations") or []
        checks["projection.mutations"] = (len(mutations) >= len(edits)
            and all(m.get("evidence_level") == "exact" for m in mutations)) if edits else not mutations
        tests = [step for step in scenario.tool_steps if step["op"] == "shell" and "unittest" in step.get("command", "")]
        checks["projection.verification"] = (any(v.get("result") == ("passed" if tests[-1].get("success", True) else "failed")
            for v in projection.get("verification_actions") or []) if tests else not projection.get("verification_actions"))
    if scenario.name.startswith('rag-'):
        checks.update(rag_material_proof(report, scenario, workspace=report.get('workspace'), projection=projection)['checks'])
    return {"verified": all(checks.values()), "checks": checks,
        "failures": [key for key, passed in checks.items() if not passed]}


def _issued_tool_matches(call, span, *, runtime_type, private=False):
    """Bind exported facts to a physical provider call, with explicit mappings.

    Codex's established Shell canonical form retains the command; its custom
    apply_patch carrier unwraps input text. Other native tools retain the full
    typed argument object. Privacy replaces arguments with a redaction marker.
    """
    function = call.get("function") or {}
    name, arguments = function.get("name"), _json_value(function.get("arguments"))
    if not isinstance(name, str) or not name or arguments is None:
        return False
    if runtime_type in {"codex", "antcodex"}:
        if name in {"exec_command", "shell_command", "read_thread_terminal", "write_stdin"}:
            name = "Bash"
            if isinstance(arguments, dict):
                command = next((arguments[key] for key in ("command", "cmd", "chars") if key in arguments), None)
                if command is not None:
                    arguments = {"command": command}
        elif name == "apply_patch" and isinstance(arguments, dict) and set(arguments) == {"input"}:
            arguments = arguments["input"]
    attrs = span.get("attributes") or {}
    exported_name = attrs.get("gen_ai.tool.name")
    if isinstance(exported_name, str) and exported_name.startswith("tool "):
        exported_name = exported_name[5:]
    exported_arguments = attrs.get("gen_ai.tool.call.arguments")
    return (exported_name == name and exported_arguments is not None
        and (_json_value(exported_arguments) == "[REDACTED]" if private else json.dumps(
            _json_value(exported_arguments), sort_keys=True, ensure_ascii=False)
            == json.dumps(arguments, sort_keys=True, ensure_ascii=False)))


def source_proof(*, result, exports, requests, scenario, mode, workspace, bounds=None, resource=None):
    checks = {}
    canceled = scenario.name == "cancel"
    checks["native.session"] = bool(result.get("session_id"))
    checks["native.completed"] = (result.get("details", {}).get("cancelled") is True
        if canceled else result.get("exit_code") == 0)
    checks["native.timeout"] = result.get("details", {}).get("timed_out") is not True
    # Use original admitted transport exports, never a platform trial response.
    admitted = [e for e in exports if e.get("admitted") is True]
    checks["native.ingress"] = bool(admitted) and len(admitted) == len(exports)
    spans = {s["span_id"]: s for e in admitted for s in e.get("spans", [])}
    roots = [s for s in spans.values() if not s.get("parent_span_id")]
    checks["native.root_trace"] = len(roots) == 1 and bool(re.fullmatch(r"[0-9a-f]{32}", roots[0].get("trace_id", "")))
    checks["native.span_identity"] = bool(spans) and all(re.fullmatch(r"[0-9a-f]{16}", key) for key in spans)
    checks["native.real_clock"] = bool(spans) and bounds is not None and all(
        type(s.get("start_time_unix_nano")) is int
        and bounds[0] <= s["start_time_unix_nano"] <= s.get("end_time_unix_nano", 0) <= bounds[1]
        for s in spans.values())
    checks["native.session_binding"] = bool(spans) and all(
        s.get("attributes", {}).get("gen_ai.conversation.id") == result.get("session_id") for s in spans.values())
    checks["native.instance_binding"] = bool(spans) and bool(resource) and all(
        all((s.get("resource", {}).get(k, s.get("attributes", {}).get(k)) if k == "ant.username"
             else s.get("resource", {}).get(k)) == v for k, v in resource.items()) for s in spans.values())
    checks["native.trace_tree"] = bool(roots) and all(
        s["trace_id"] == roots[0]["trace_id"] and (not s.get("parent_span_id")
        or s["parent_span_id"] in spans) for s in spans.values())
    checks["provider.observed"] = bool(requests)
    checks["provider.errors"] = all(not r.get("error") for r in requests) if not canceled else True
    if not canceled:
        agents = [r for r in requests if r.get("purpose") == "agent" and r.get("completed")]
        observed = {item.get("tool_call_id"): item.get("content") for item in agents[-1].get("request", {}).get("messages", [])
                    if item.get("role") == "tool"} if agents else {}
        calls = [call for r in agents for call in r.get("response", {}).get("tool_calls", [])]
        issued = {call.get("id") for call in calls}
        tools = {s.get("attributes", {}).get("gen_ai.tool.call.id"): s for s in spans.values()
                 if s.get("attributes", {}).get("gen_ai.operation.name") == "execute_tool"}
        if mode == "fake":
            checks["provider.actual_tool_results"] = bool(agents) and len([
                item for item in agents[-1].get("request", {}).get("messages", [])
                if item.get("role") == "tool"]) == len(scenario.tool_steps)
        checks["native.tool_call_binding"] = bool(issued) and None not in issued and set(tools) == issued == set(observed)
        checks["native.tool_input_binding"] = bool(calls) and all(
            call.get("id") in tools and _issued_tool_matches(call, tools[call["id"]],
                runtime_type=str((resource or {}).get("ant.agent.type", "")).lower(),
                private=scenario.name == "privacy") for call in calls)
        if scenario.name != "privacy":
            checks["native.tool_result_binding"] = bool(tools) and all(
                s["attributes"].get("gen_ai.tool.call.result") == observed.get(call_id)
                for call_id, s in tools.items())
        checks["native.llm_source"] = any(s.get("attributes", {}).get("gen_ai.operation.name") == "chat" for s in spans.values())
    else:
        checks["native.cancel_signal"] = any(
            s.get("attributes", {}).get("error.type") in {"cancelled", "canceled", "aborted"}
            for s in roots)
    if scenario.name == "privacy":
        wire = json.dumps(exports)
        checks["native.privacy"] = scenario.prompt not in wire and all(
            line.strip() not in wire for content in scenario.files.values()
            for line in content.splitlines() if line.strip())
    edited = any(step["op"] == "edit" for step in scenario.tool_steps)
    if edited:
        path = Path(workspace) / "app.py"
        expected = (scenario.final_files or {}).get("app.py", "def add(a, b):\n    return a + b\n")
        checks["workspace.final_state"] = path.is_file() and path.read_text() == expected
    if scenario.name.startswith('rag-'):
        report = {'app': 'codex' if (resource or {}).get('ant.agent.type') == 'codex' else 'dsh02',
                  'exports': exports, 'requests': requests, 'result': result}
        checks.update(rag_material_proof(report, scenario, workspace=workspace)['checks'])
    return {"verified": all(checks.values()), "checks": checks,
        "failures": [k for k, value in checks.items() if not value],
        "source_trace_ids": sorted({s["trace_id"] for s in roots}),
        "source_span_ids": sorted(spans)}
