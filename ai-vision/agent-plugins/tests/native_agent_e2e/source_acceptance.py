"""Exact native-source acceptance, retaining provider fidelity as a separate fact.

Only exact recorded DSH/Hermes/Kimi pins may accept explicit host usage losses. Raw
requests and the strict oracle are never edited. A separate expected-usage view
uses DSH canonical JSONL or Hermes raw callbacks to check original exports
through the structural oracle, with independent source identity, message and
usage checks. No production plugin helper is used.
"""
from copy import deepcopy

from dsh_source import _usage, _flag
from evidence_integrity import exports_match_raw
from verify import kind, token_metric_checks, valid_count, verify_case

PINS = {'dsh01': '0.1.7-rc.2', 'dsh02': '0.2.0-rc.2'}
LIMITATIONS = {'unknown-usage': 'dsh.host.usage_zero_initialization',
               'cache-usage': 'dsh.host.reasoning_tokens_unavailable'}


def _arguments(report, *, requests=None, scenario=None):
    models={call.get('request',{}).get('model') for call in report.get('requests',[]) if call.get('response') and call.get('method')!='GET'}
    configured=report.get('model') or (next(iter(models)) if len(models)==1 else 'native-e2e-chat')
    return dict(app=report['app'], scenario=scenario or report['scenario'], model=configured,
        receipt=report['receipt'], result=report['result'], requests=report['requests'] if requests is None else requests,
        exports=report['exports'], expected_resource=report.get('expected_resource'), mode=report.get('mode', 'fake'))


def _known_native_calls(report):
    app, scenario = report.get('app'), report.get('scenario')
    if (app not in PINS or scenario not in LIMITATIONS or report.get('mode') != 'fake'
            or report.get('version') != PINS[app] or report.get('result', {}).get('version') != PINS[app]
            or report.get('case') != app + '-' + scenario
            or report.get('model', 'native-e2e-chat') != 'native-e2e-chat'): return None
    details = report['result'].get('details') or {}
    header, events = details.get('canonical_header'), details.get('canonical_events')
    if not isinstance(header, dict) or header.get('version') != 4 or not isinstance(events, list): return None
    native = [r.get('data', {}).get('usage') for r in events if r.get('type') == 'assistant/message']
    calls = [r for r in report.get('requests', []) if r.get('response') and r.get('method') != 'GET']
    # These two single-turn fake scenes have exactly one read and one answer.
    if len(native) != len(calls) or len(calls) != 2 or any(r.get('purpose') == 'auxiliary'
            or type(r.get('status')) is not int or r['status'] != 200
            or ('completed' in r and r['completed'] is not True)
            or ('client_disconnected' in r and r['client_disconnected'] is not False) for r in calls): return None
    # FakeProvider records finish_reason only after terminal [DONE] write and
    # flush succeeds. canonical_matches separately requires both completed
    # assistant/step pairs and the completed native turn. A nonexistent generic
    # completed flag is not used as a substitute for these physical/host facts.
    if [r.get('finish_reason') for r in calls] != ['tool_calls', 'stop'] or any(r.get('request', {}).get('model') != 'native-e2e-chat' for r in calls): return None
    projected = deepcopy(report['requests']); position = 0
    for call in projected:
        if not call.get('response') or call.get('method') == 'GET': continue
        canonical, wire = native[position], call.get('usage'); position += 1
        if scenario == 'unknown-usage':
            if wire is not None or canonical != {'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0} or any(type(v) is not int for v in canonical.values()): return None
        else:
            if (not isinstance(canonical, dict) or set(canonical) != {'inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens', 'cacheWriteTokens'}
                    or not isinstance(wire, dict)): return None
            reasoning = (wire.get('completion_tokens_details') or {}).get('reasoning_tokens')
            if not valid_count(reasoning) or reasoning <= 0 or not _usage({**canonical, 'reasoningTokens': reasoning}, wire): return None
        call['usage'] = {'prompt_tokens': canonical['inputTokens'] + canonical.get('cacheReadTokens', 0) + canonical.get('cacheWriteTokens', 0),
            'completion_tokens': canonical['outputTokens'], 'total_tokens': canonical['totalTokens']}
        if 'cacheReadTokens' in canonical or 'cacheWriteTokens' in canonical:
            call['usage']['prompt_tokens_details'] = {key: canonical[field] for key, field in
                (('cached_tokens', 'cacheReadTokens'), ('cache_write_tokens', 'cacheWriteTokens')) if field in canonical}
    return projected, native


def _canonical_signals(report, projected, native):
    spans = [s for e in report['exports'] for s in e.get('spans', [])]
    logs = [s for e in report['exports'] for s in e.get('logs', []) if s.get('attributes', {}).get('event.name') == 'api_request']
    metrics = [s for e in report['exports'] for s in e.get('metrics', [])]
    generations = sorted([s for s in spans if kind(s) == 'LLM'], key=lambda s: s.get('start_time_unix_nano', 0))
    native_by_id = {(s.get('trace_id'), s.get('span_id')): usage for s, usage in zip(generations, native)}
    checks = {}
    fields = (('totalTokens', ('gen_ai.usage.total_tokens',)),
        ('inputTokens', ('agentic.usage.fresh_input_tokens',)),
        ('cacheReadTokens', ('gen_ai.usage.cache_read.input_tokens', 'agentic.usage.cache_read_tokens')),
        ('cacheWriteTokens', ('gen_ai.usage.cache_creation.input_tokens', 'agentic.usage.cache_write_tokens')),
        ('reasoningTokens', ('gen_ai.usage.reasoning.output_tokens', 'agentic.usage.reasoning_tokens')))
    for signal, rows in (('spans', generations), ('logs', logs)):
        exact = len(rows) == len(native)
        for row in rows:
            attrs = row.get('attributes') or {}; canonical = native_by_id.get((row.get('trace_id'), row.get('span_id')))
            if canonical is None: exact = False; continue
            for field, aliases in fields:
                for attribute in aliases:
                    if field in canonical:
                        exact &= valid_count(attrs.get(attribute)) and attrs[attribute] == canonical[field]
                    else: exact &= attribute not in attrs
            exact &= all(_flag(attrs.get('agentic.usage.' + direction + '_tokens.missing'), False) for direction in ('input', 'output'))
        checks['canonical.' + signal + '.usage'] = bool(exact)
    # L1 counts input including cache; agent.token.usage counts fresh input.
    # Each direction needs a real histogram, including reported-zero samples.
    for name, inclusive in (('agent.token.usage', False), ('gen_ai.client.token.usage', True)):
        rows = [m for m in metrics if m.get('name') == name]
        copied = [dict(m, name='agent.token.usage') for m in rows]
        if not inclusive: copied += [m for m in metrics if m.get('name') in ('agent.cache.read_tokens', 'agent.cache.write_tokens')]
        calls = deepcopy([r for r in projected if r.get('response') and r.get('method') != 'GET'])
        if inclusive:
            for call in calls: call['usage'].pop('prompt_tokens_details', None)
        checks['canonical.metrics.' + name] = (set(m.get('attributes', {}).get('gen_ai.token.type', m.get('attributes', {}).get('type')) for m in rows) == {'input', 'output'}
            and all(m.get('kind') == 'histogram' for m in rows) and token_metric_checks(copied, calls))
    return {'verified': bool(checks) and all(checks.values()), 'checks': checks,
            'failures': [k for k, v in checks.items() if not v]}


def assess_source(report, *, strict=None):
    """Recompute source facts by default; flags in saved reports are not evidence."""
    try:
        strict = strict if strict is not None else verify_case(**_arguments(report))
    except (KeyError, TypeError, ValueError, AttributeError):
        strict = {'verified': False, 'checks': {}, 'failures': ['source.missing_or_invalid_evidence']}
    if not exports_match_raw(report.get('exports')):
        strict=deepcopy(strict)
        strict.setdefault('checks', {})['source.raw_integrity']=False
        strict.setdefault('failures', []).append('source.raw_integrity')
        strict['verified']=False
        return {'source':strict,'provider_reconciled':False,'canonical_verified':False,'known_limitations':[],
                'accepted':False,'status':'failed'}
    accepted = strict.get('verified') is True
    if report.get('app') in PINS:
        accepted &= report.get('version') == PINS[report['app']] and report.get('result', {}).get('version') == PINS[report['app']]
    answer = {'source': strict, 'provider_reconciled': accepted, 'canonical_verified': accepted,
              'known_limitations': [], 'accepted': accepted, 'status': 'passed' if accepted else 'failed'}
    if accepted: return answer
    if report.get('app') == 'hermes':
        from hermes_source import assess_hermes_source
        return assess_hermes_source(report, strict_result=strict)
    if report.get('app') == 'kimi':
        from kimi_source import assess_kimi_source
        return assess_kimi_source(report, strict_result=strict)
    try:
        evidence = _known_native_calls(report)
        if evidence is None: return answer
        projected, native = evidence
        # Usage comes from canonical JSONL. The original scene's raw facts are
        # separately bounded above; lifecycle/tool/IO/identity checks are shared
        # with the otherwise identical single-turn tool loop, without editing
        # the original source proof or recorded scenario.
        canonical = verify_case(**_arguments(report, requests=projected, scenario='tool-loop'))
        signals = _canonical_signals(report, projected, native)
        canonical['checks'].update(signals['checks']); canonical['failures'] += signals['failures']
        canonical['verified'] = not canonical['failures']
        answer.update(canonical=canonical, canonical_verified=canonical['verified'])
        if canonical['verified']:
            answer.update(accepted=True, status='passed_with_known_limitations', known_limitations=[{
                'id': LIMITATIONS[report['scenario']], 'app': report['app'], 'version': report['version'],
                'scenario': report['scenario'], 'calls': [0, 1]}])
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return answer


def bounded_row(row):
    """Validate summary declarations; artifact consumers also recompute facts."""
    if row.get('case') == 'hermes-zero-usage':
        from hermes_source import bounded_hermes_row
        return bounded_hermes_row(row)
    if row.get('case', '').startswith('kimi-'):
        from kimi_source import bounded_kimi_row
        return bounded_kimi_row(row)
    name = row.get('case', ''); app, _, scenario = name.partition('-')
    known = row.get('known_limitations')
    return (app in PINS and scenario in LIMITATIONS and row.get('version') == PINS[app]
        and row.get('status') == 'passed_with_known_limitations' and row.get('accepted') is True
        and row.get('verified') is False and row.get('source_verified') is False
        and row.get('provider_reconciled') is False and row.get('canonical_verified') is True
        and known == [{'id': LIMITATIONS[scenario], 'app': app, 'version': PINS[app], 'scenario': scenario, 'calls': [0, 1]}])
