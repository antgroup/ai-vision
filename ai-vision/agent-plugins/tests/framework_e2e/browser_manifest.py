"""Project verified SDK evidence into a public, credential-free browser contract.

This command reads only recognized local evidence files. It never contacts a
platform or uses an API response as the expected page content.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from verify_rag import (canonical_usage, decoded, observation_type,
                        projected_metadata, span_usage, status_code)

FIELDS = ('input', 'output', 'total')
PUBLIC_TARGET = {'spaceId', 'agentUuid', 'agentProduct'}
JS_CORE_CHECKS = ('native-http', 'complete', 'inventory', 'parents', 'error-status', 'raw-io',
                  'timestamps', 'independent-usage', 'v1-http', 'v1-inventory',
                  'sdk-generation-usage', 'native-generation-usage', 'usage-provenance',
                  'native-usage-details', 'v1-generation-usage')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or linked evidence file: ' + path.name)
    return json.loads(path.read_text(encoding='utf-8'))


def public_target(value):
    require(isinstance(value, dict) and set(value) == PUBLIC_TARGET, 'Public target must contain only spaceId, agentUuid, agentProduct')
    require(all(isinstance(v, str) and v.strip() == v and v for v in value.values()), 'Invalid public target identity')
    return dict(value)


def usage(value):
    result = {key: value.get(key) if isinstance(value, dict) else None for key in FIELDS}
    require(all(item is None or (type(item) is int and item >= 0) for item in result.values()), 'Usage must be nonnegative integers or null')
    return result


def aggregate(values):
    return {key: sum(row[key] for row in values) if values and all(row[key] is not None for row in values) else None for key in FIELDS}


def passed_checks(proof):
    checks = proof.get('checks')
    return isinstance(checks, dict) and bool(checks) and all(value is True for value in checks.values()) and not proof.get('failures')


def require_checks(checks, names, description):
    require(all(checks.get(name) is True for name in names), 'Missing required ' + description + ' proof')


def python_fake_proof(report, proof, trace_ids):
    checks = proof['checks']
    require_checks(checks, ('report_schema', 'framework_run_completed', 'sdk_ingestion_accepted',
        'declared_roots_unique', 'exact_trace_inventory', 'span_ids_unique', 'parent_graph_same_trace_acyclic',
        'platform.exact_window', 'platform.source_traces_present', 'platform.session_usage_http',
        'platform.session_io_http', 'platform.session_inventory'), 'Python source and platform')
    metadata = report.get('metadata_contract') == 'python-sdk-json-types-v1'
    if metadata:
        major = int(report['versions']['langfuse'].split('.')[0])
        names = ['sdk_python_wire_version', 'sdk_python_http_protobuf']
        if major >= 5:
            names += ['sdk_python_default_gzip', 'sdk_python_ingestion_v4']
        require_checks(checks, names, 'Python SDK wire format')
    # The verifier numbers native/v1 checks by sorted SDK trace ID, independently
    # of report/session order. Require every trace, not just a successful total.
    for index, tid in enumerate(sorted(trace_ids)):
        root = 'root.' + tid + '.'
        names = [root + name for name in ('one_root', 'business_user', 'status', 'application_status', 'actual_io')]
        if metadata:
            names.append(root + 'metadata_wire_types')
            if major >= 5:
                names.append(root + 'metadata_json_types')
        names += [f'platform.native.{index}.' + name for name in ('http', 'exact_spans', 'source_fields', 'business_user')]
        names += [f'platform.v1.{index}.' + name for name in ('trace', 'pagination', 'exact_observations')]
        require_checks(checks, names, 'Python per-trace')
    for index, _session in enumerate(report.get('sessions', [])):
        names = [f'platform.session.{index}.' + name for name in ('usage', 'io')]
        names += [f'platform.v1.session.{index}.' + name for name in
                  ('identity', 'trace_inventory', 'business_user', 'trace_fields', 'created')]
        require_checks(checks, names, 'Python session')


def span_graph(spans):
    require(bool(spans), 'Empty SDK span inventory')
    ids = [item['spanId'] for item in spans]
    require(all(isinstance(value, str) and value for value in ids) and len(set(ids)) == len(ids), 'Missing or duplicate span IDs')
    index = {item['spanId']: item for item in spans}
    roots = [item for item in spans if item['parentSpanId'] is None]
    require(len(roots) == 1, 'Trace requires exactly one SDK root')
    for item in spans:
        visited = set()
        current = item
        while current['parentSpanId'] is not None:
            parent = current['parentSpanId']
            require(parent in index and parent not in visited, 'Missing parent or cyclic SDK graph')
            visited.add(parent)
            current = index[parent]
        require(item['_from'] <= item['_to'], 'Reversed span timestamps')
    return roots[0]


def nano(value):
    require(type(value) is int or isinstance(value, str) and value.isdigit(), 'Missing exact SDK timestamp')
    answer = int(value)
    require(answer > 0, 'Invalid SDK timestamp')
    return answer


def source_io(attrs, field, *, preserve_wire=False):
    """Independent source-presence oracle; decoded null is not absence.

    Explicit OTel messages win even when their value is null. Derived messages
    defer to the original SDK value. Other fallbacks must actually hold a value;
    an SDK's serialized JSON 'null' is present and is decoded exactly once.
    """
    standard = f'gen_ai.{field}.messages'
    sources = decoded(attrs.get('aivision.normalization.sources'))
    sources = sources if isinstance(sources, dict) else {}
    if standard in attrs and standard not in sources:
        return attrs[standard] if preserve_wire else decoded(attrs[standard]), True
    keys = (f'langfuse.observation.{field}', f'{field}.value', standard,
            'gen_ai.tool.call.arguments' if field == 'input' else 'gen_ai.tool.call.result',
            f'aivision.request.{field}')
    for key in keys:
        if attrs.get(key) is not None:
            return attrs[key] if preserve_wire else decoded(attrs[key]), True
    return None, False


def python_span(value):
    attrs = value.get('attributes', {})
    input_value, input_present = source_io(attrs, 'input')
    output_value, output_present = source_io(attrs, 'output')
    return {'spanId': value.get('span_id'), 'parentSpanId': value.get('parent_span_id') or None,
            'name': value['name'], 'type': observation_type(value), 'input': input_value,
            'output': output_value, 'inputPresent': input_present, 'outputPresent': output_present,
            'inputWire': source_io(attrs, 'input', preserve_wire=True)[0],
            'outputWire': source_io(attrs, 'output', preserve_wire=True)[0],
            'usage': usage(span_usage(value)),
            'expectedError': status_code(value.get('status')) == 2 or attrs.get('langfuse.observation.level') == 'ERROR',
            'metadata': projected_metadata(attrs, 'langfuse.observation.metadata', value),
            '_from': nano(value.get('start_time_unix_nano')), '_to': nano(value.get('end_time_unix_nano'))}


def trace_record(target, spans, *, tid, sid, framework, version, language, scenario, expected_usage):
    require(isinstance(tid, str) and bool(tid), 'Missing trace ID')
    require(sid is None or isinstance(sid, str) and bool(sid), 'Invalid session ID')
    require(isinstance(version, str) and bool(version), 'Missing SDK version')
    root = span_graph(spans)
    return {**target, 'label': f'{language}-{version}-{framework}-{scenario}-{tid}',
            'framework': framework, 'sdkVersion': version, 'sdkLanguage': language,
            'scenario': scenario, 'traceId': tid, 'sessionId': sid, 'rootName': root['name'],
            'rootSpanId': root['spanId'], 'input': root['input'], 'output': root['output'],
            'inputPresent': root['inputPresent'], 'outputPresent': root['outputPresent'],
            'usage': usage(expected_usage), 'expectedError': any(item['expectedError'] for item in spans),
            'sourceVerified': True, 'platformVerified': True, 'spans': spans,
            '_from': min(item['_from'] for item in spans), '_to': max(item['_to'] for item in spans)}


def python_directories(root, source):
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked Python evidence directory')
    if (root/'report.json').is_file():
        return [root]
    require(source == 'fake', 'Live Python root must contain report.json')
    summary = read(root/'summary.json')
    cases = summary.get('cases', [])
    require(summary.get('status') == 'passed' and summary.get('mode') in ('ci', 'platform') and cases,
            'Python suite must pass real-platform acceptance')
    require(len(cases) == summary.get('executed_cases') == summary.get('expected_cases'), 'Partial Python suite')
    names = [case.get('case') for case in cases]
    require(len(set(names)) == len(names), 'Duplicate Python cases')
    directories = []
    for case, name in zip(cases, names):
        require(isinstance(name, str) and Path(name).name == name and name not in ('.', '..'), 'Unsafe case path')
        require(case.get('execute_exit') == case.get('verify_exit') == 0 and case.get('source_verified') is True
                and case.get('platform_verified') is True, 'Unverified Python suite case')
        directories.append(root/name)
    return directories


def python_records(directory, source, target):
    report = read(directory/'report.json')
    exports = read(directory/'sdk-exports.json')
    require(report.get('status') == 'passed', 'Failed Python execution evidence')
    require(isinstance(exports, list) and exports, 'Empty Python SDK exports')
    spans = [item for export in exports for item in export.get('spans', [])]
    by_trace = defaultdict(list)
    for item in spans:
        by_trace[item.get('trace_id')].append(item)
    if source == 'fake':
        proof = read(directory/'verification.json')
        require(proof.get('passed') is True and proof.get('source_verified') is True
                and proof.get('platform_query_verified') is True and passed_checks(proof), 'Unverified Python source or platform evidence')
        require(report.get('sdk') == 'langfuse', 'Browser SDK profile requires Langfuse')
        identity = report.get('identity', {})
        require(all(identity.get(old) == target[new] for old, new in
                    [('space_id', 'spaceId'), ('agent_uuid', 'agentUuid'), ('agent_product', 'agentProduct')]), 'Python target identity mismatch')
        cases = [{**root, 'session_id': session['session_id']} for session in report.get('sessions', []) for root in session.get('roots', [])]
        provider = None
    else:
        require(report.get('mode') == 'platform' and passed_checks(report), 'Unverified live Python evidence')
        require(public_target(report.get('identity')) == target, 'Live Python target identity mismatch')
        require(report.get('provider_base_url') == 'https://integrate.api.nvidia.com/v1', 'Live source is not NVIDIA')
        cases = report.get('cases', [])
        provider = read(directory/'provider-ledger.json')
        require(len(provider) == len(cases), 'Incomplete live Python provider ledger')
    ids = [case.get('trace_id') for case in cases]
    require(ids and len(set(ids)) == len(ids) and set(ids) == set(by_trace), 'Python report and SDK trace inventory mismatch')
    if source == 'fake':
        python_fake_proof(report, proof, ids)
    bounds = report.get('bounds', {})
    start, end = (bounds.get(key) for key in (('from_ms', 'to_ms') if source == 'fake' else ('from', 'to')))
    require(type(start) is int and type(end) is int and start < end, 'Missing or invalid Python execution window')
    answer = []
    for case in cases:
        own = by_trace[case['trace_id']]
        projected = [python_span(item) for item in own]
        if provider is None:
            expected = canonical_usage(own)
            scenario = report['scenario']
            sid = case['session_id']
            framework = report['framework']
        else:
            scenario = case['case']
            checks = report['checks']
            require(all(checks.get(scenario + '.' + check) is True for check in
                        ('single_request', 'single_generation', 'unique_spans', 'native_http', 'native_complete',
                         'native_identity', 'native_readback', 'native_provider_usage', 'v1_trace', 'v1_pagination',
                         'v1_observations', 'v1_provider_usage', 'v1_session_identity', 'v1_session_inventory',
                         'v1_session_traces', 'v1_session_created')),
                    'Missing live Python per-case proof')
            require_checks(checks, [scenario + '.' + check for check in
                (('actual_error', 'error_observation', 'unknown_usage') if scenario == 'provider-error' else
                 ('actual_output', 'provider_usage', 'sdk_usage'))], 'live Python source accounting')
            raw = [row for row in provider if row.get('case') == scenario]
            require(len(raw) == 1 and not raw[0].get('proxy_error'), 'Missing or failed provider request')
            raw = raw[0]
            if scenario == 'provider-error':
                require(raw.get('status') in (400, 404, 422) and raw.get('usage') is None, 'Invalid provider error evidence')
                expected = usage(None)
            else:
                require(raw.get('status') == 200 and isinstance(raw.get('usage'), dict), 'Invalid provider success evidence')
                expected = usage({key: raw['usage'].get(provider_key) for key, provider_key in
                                  zip(FIELDS, ('prompt_tokens', 'completion_tokens', 'total_tokens'))})
                require(all(value is not None for value in expected.values()) and expected['input'] + expected['output'] == expected['total']
                        and expected['output'] <= 32, 'Invalid provider usage')
            generations = [item for item in projected if item['type'] == 'GENERATION']
            require(len(generations) == 1 and generations[0]['usage'] == expected, 'Provider and SDK usage mismatch')
            sessions = {item.get('attributes', {}).get('session.id') or item.get('attributes', {}).get('langfuse.session.id') for item in own}
            sessions.discard(None)
            require(len(sessions) == 1, 'Missing or ambiguous live Python session')
            sid = sessions.pop()
            framework = 'langchain' if scenario.startswith('langchain') else 'openai'
        record = trace_record(target, projected, tid=case['trace_id'], sid=sid, framework=framework,
                              version=report.get('versions', {}).get('langfuse'), language='python', scenario=scenario,
                              expected_usage=expected)
        require(start * 1000000 <= record['_from'] <= record['_to'] <= end * 1000000,
                'SDK timestamps outside Python execution window')
        if provider is None:
            require(record['input'] == case.get('input') and record['output'] == case.get('output'), 'Application and SDK root IO mismatch')
            recorded_sessions = {item.get('attributes', {}).get('session.id') or
                                 item.get('attributes', {}).get('langfuse.session.id') for item in own}
            recorded_sessions.discard(None)
            require(recorded_sessions == {sid}, 'Application and SDK session identity mismatch')
        answer.append(record)
    return answer


def js_records(directory, source, target):
    require(directory.is_dir() and not directory.is_symlink(), 'Missing or linked JS evidence directory')
    live = source == 'nvidia'
    ledgers = [read(directory/('live-ledger.json' if live else 'ledger.json'))]
    if not live:
        ledgers.append(read(directory/'ledger.json.lifecycle.json'))
    proof = read(directory/('live-readback.json' if live else 'platform-readback.json'))
    require(proof.get('passed') is True and not proof.get('failures') and proof.get('results')
            and proof.get('checks') == len(proof['results']) and all(row.get('passed') is True for row in proof['results']),
            'Failed or incomplete JS platform proof')
    require(public_target(proof.get('identity')) == target, 'JS target identity mismatch')
    records = []
    for ledger in ledgers:
        require(isinstance(ledger, list), 'Invalid JS ledger')
        for row in ledger:
            if row.get('collectorOnly'):
                continue
            if 'expectedByTrace' in row:
                require(row['expectedByTrace'], 'Empty JS per-trace source inventory')
                records.extend([{**item, 'scenario': row['scenario'], 'sdkVersion': row['sdkVersion']} for item in row['expectedByTrace']])
            else:
                records.append({**row, 'scenario': row.get('variant', row.get('scenario')), 'usage': row.get('expectedUsage')})
    require(records and proof.get('traces') == len(records), 'JS source and platform trace counts differ')
    results = {row['name'] for row in proof['results']}
    require(len(results) == len(proof['results']), 'Duplicate JS readback checks')
    answer = []
    for record in records:
        scenario, tid = record['scenario'], record['traceId']
        required = list(JS_CORE_CHECKS)
        for field, check in [('sessionId', 'session'), ('userId', 'business-user'),
                             ('expectedGenerationCount', 'generations'), ('metadata', 'typed-metadata')]:
            if field in record and record[field] is not None:
                required.append(check)
        require(all(f'{scenario}:{tid}:{check}' in results for check in required), 'JS trace lacks exact readback proof')
        expected = usage(record.get('usage'))
        if live:
            reference = record.get('providerReference', {})
            require(record.get('provider') == 'integrate.api.nvidia.com', 'Live source is not NVIDIA')
            require(usage(reference.get('usage')) == expected, 'JS provider and expected usage differ')
            if scenario == 'provider-error':
                require(reference.get('httpStatus') in (400, 404, 422) and reference.get('usage') is None, 'Invalid JS provider error')
            else:
                require(reference.get('httpStatus') == 200 and all(v is not None for v in expected.values())
                        and expected['input'] + expected['output'] == expected['total'] and expected['output'] <= 32,
                        'Invalid JS provider success')
        spans = []
        inventory = record.get('inventory', [])
        generation_count = sum(str(item.get('type', '')).upper() == 'GENERATION' for item in inventory)
        require(generation_count <= 1 or all(value is None for value in expected.values()), 'Cannot allocate aggregate JS usage to multiple generations')
        for item in inventory:
            kind = str(item.get('type') or 'SPAN').upper()
            # sourceInventory preserves only the two original Langfuse IO
            # attributes, and JSON serialization omits an absent (undefined)
            # key. Never infer presence from its normalized/decoded value.
            original_io = {f'langfuse.observation.{field}': item[field]
                           for field in ('input', 'output') if field in item}
            input_value, input_present = source_io(original_io, 'input')
            output_value, output_present = source_io(original_io, 'output')
            spans.append({'spanId': item.get('id'), 'parentSpanId': item.get('parentId') or None, 'name': item['name'],
                          'type': kind, 'input': input_value, 'output': output_value,
                          'inputPresent': input_present, 'outputPresent': output_present,
                          'inputWire': source_io(original_io, 'input', preserve_wire=True)[0],
                          'outputWire': source_io(original_io, 'output', preserve_wire=True)[0],
                          'usage': expected.copy() if kind == 'GENERATION' and generation_count == 1 else usage(None),
                          'expectedError': status_code(item.get('status', 0)) == 2 or item.get('level') == 'ERROR',
                          '_from': nano(item.get('startTimeUnixNano')), '_to': nano(item.get('endTimeUnixNano'))})
        root = span_graph(spans)
        if isinstance(record.get('metadata'), dict):
            # JS ledger already holds the independent v5/v6 public metadata oracle.
            root['metadata'] = record['metadata']
        answer.append(trace_record(target, spans, tid=tid, sid=record.get('sessionId'),
                                   framework='langchain' if scenario.startswith('langchain-') else 'openai' if live else 'javascript-sdk',
                                   version=record.get('sdkVersion'), language='javascript', scenario=scenario, expected_usage=expected))
    return answer


def iso(value):
    return datetime.fromtimestamp(value / 1000000000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def build_manifest(source, python_roots, js_roots, target):
    require(source in ('fake', 'nvidia'), 'Unknown evidence source')
    target = public_target(target)
    traces = []
    for root in python_roots:
        for directory in python_directories(Path(root), source):
            traces.extend(python_records(directory, source, target))
    for root in js_roots:
        traces.extend(js_records(Path(root), source, target))
    require(traces, 'Empty browser manifest')
    identities = [(row['agentUuid'], row['traceId']) for row in traces]
    require(len(set(identities)) == len(identities), 'Duplicate trace IDs')
    traces.sort(key=lambda row: (row['_from'], row['traceId']))
    sessions = defaultdict(list)
    for trace in traces:
        if trace['sessionId'] is not None:
            sessions[trace['sessionId']].append(trace)
    session_rows = [{**target, 'label': sid, 'sessionId': sid, 'traceIds': [row['traceId'] for row in rows],
                     'turns': [{'traceId': row['traceId'], 'input': row['input'], 'output': row['output'],
                                'inputPresent': row['inputPresent'], 'outputPresent': row['outputPresent']} for row in rows],
                     'usage': aggregate([row['usage'] for row in rows])} for sid, rows in sessions.items()]
    window = {'from': iso(min(row['_from'] for row in traces) - 1000000),
              'to': iso(max(row['_to'] for row in traces) + 1000000)}
    for trace in traces:
        for item in [trace, *trace['spans']]:
            item.pop('_from'); item.pop('_to')
    return {'schemaVersion': 1, 'source': source, 'window': window, 'traces': traces, 'sessions': session_rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, choices=('fake', 'nvidia'))
    parser.add_argument('--python-root', type=Path, action='append', default=[])
    parser.add_argument('--js-root', type=Path, action='append', default=[])
    parser.add_argument('--target-public', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(not args.out.exists(), 'Use a fresh manifest path')
        manifest = build_manifest(args.source, args.python_root, args.js_root, read(args.target_public))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x', encoding='utf-8') as output:
            json.dump(manifest, output, indent=2, ensure_ascii=False)
            output.write('\n')
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'Browser evidence rejected: {error}\n')
    print(json.dumps({'source': args.source, 'traces': len(manifest['traces']), 'sessions': len(manifest['sessions'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
