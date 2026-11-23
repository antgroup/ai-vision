"""Cross-platform agent-framework and public-API acceptance entrypoint."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


SDK_COVERAGE_SCENARIOS = {
    'full': ('success', 'no-results', 'retrieval-error', 'retry', 'changed-corpus', 'zero-usage',
             'unknown-usage', 'stream', 'cancel', 'concurrent', 'tool-exception'),
    'smoke': ('success', 'unknown-usage', 'stream', 'cancel', 'concurrent'),
}


def acceptance_status(records, expected_count, platform):
    return 'passed' if len(records) == expected_count and expected_count > 0 and all(
        r['execute_exit'] == r['verify_exit'] == 0 and r['source_verified'] and
        (not platform or r['platform_verified']) for r in records) else 'failed'


def scenarios(coverage='full'):
    return list(SDK_COVERAGE_SCENARIOS[coverage])


def ci_backend(value):
    if value not in ('oceanbase', 'seekdb'):
        raise ValueError('CI requires explicit OceanBase or seekdb; no storage fallback is allowed')
    return value


def validate_ci_targets(backend, targets, sdks=('langfuse', 'nemo-relay')):
    if not sdks or len(sdks) != len(set(sdks)) or any(sdk not in ('langfuse', 'nemo-relay') for sdk in sdks):
        raise ValueError('Choose a nonempty, unique SDK profile')
    if not all(isinstance(targets.get(sdk), dict) and targets[sdk].get('data_backend') == backend
               for sdk in sdks):
        raise ValueError('Every selected SDK target must explicitly declare the selected OceanBase/seekdb backend')


def isolated_environment():
    prefixes = ('OTEL_', 'LANGFUSE_', 'LANGCHAIN_', 'LANGSMITH_', 'OPENAI_', 'ANTHROPIC_',
                'AIVISION_OTLP_', 'AIVISION_AGENT_', 'AIVISION_ANT_')
    env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
    env.update(NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost', PYTHONUNBUFFERED='1')
    return env


def run_case(root, out, repo, framework, sdk, scenario, platform_mode, targets):
    directory = out/f'{framework}-{sdk}-{scenario}'
    directory.mkdir(parents=True, exist_ok=False)
    env = isolated_environment()
    if targets:
        env['AIVISION_E2E_TARGETS_JSON'] = json.dumps(targets)
    command = [sys.executable, str(root/'execute_rag.py'), '--framework', framework, '--sdk', sdk,
               '--scenario', scenario, '--out', str(directory), '--repo', str(repo),
               '--platform' if platform_mode else '--collector-only']
    execute = subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)
    secrets = [v['space_key'] for v in targets.values()] if targets else []
    def sanitized(value):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    (directory/'execute.log').write_text(sanitized(execute.stdout+execute.stderr), encoding='utf-8')
    verify_exit = 1
    verification = {}
    report_file, exports_file = directory/'report.json', directory/'sdk-exports.json'
    if report_file.exists() and exports_file.exists():
        command = [sys.executable, str(root/'verify_rag.py'), '--report', str(report_file),
                   '--exports', str(exports_file), '--out', str(directory/'verification.json')]
        if platform_mode:
            command.append('--platform')
        verify = subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)
        verify_exit = verify.returncode
        (directory/'verify.log').write_text(sanitized(verify.stdout+verify.stderr), encoding='utf-8')
        if (directory/'verification.json').exists():
            verification = json.loads((directory/'verification.json').read_text(encoding='utf-8'))
    result = {'case': directory.name, 'execute_exit': execute.returncode, 'verify_exit': verify_exit,
              'source_verified': verification.get('source_verified') is True,
              'platform_verified': verification.get('platform_query_verified') is True,
              'known_limits': verification.get('known_limits', []),
              'failures': verification.get('failures', []), 'report': str(report_file),
              'verification': str(directory/'verification.json')}
    print(json.dumps({k: result[k] for k in ('case', 'execute_exit', 'verify_exit', 'source_verified', 'platform_verified')}, ensure_ascii=True), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['collector', 'platform', 'ci'], default='collector',
                        help='platform and ci query a real deployment supplied through AIVISION_E2E_TARGETS_JSON')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--framework', choices=['langchain', 'deepagents'])
    sdk_group = parser.add_mutually_exclusive_group()
    sdk_group.add_argument('--sdk', choices=['langfuse', 'nemo-relay'], help='single SDK local debugging alias')
    sdk_group.add_argument('--sdks', nargs='+', choices=['langfuse', 'nemo-relay'],
                           help='explicit acceptance SDK profile; defaults to both SDKs')
    parser.add_argument('--sdk-coverage', choices=SDK_COVERAGE_SCENARIOS, default='full',
                        help='fixed SDK scenario set. Smoke requires both SDKs on the CI OceanBase deployment')
    parser.add_argument('--scenarios', help='comma-separated subset for local debugging only')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    repo = args.repo or next((p for p in root.parents if (p/'ai-vision/aivision-py-be').is_dir()), None)
    if repo is None:
        parser.error('Supply --repo pointing to this monorepo')
    if args.mode == 'ci' and any(value is not None for value in (args.framework, args.sdk, args.scenarios)):
        parser.error('CI acceptance requires both frameworks and a fixed SDK coverage profile')
    sdks = args.sdks or ([args.sdk] if args.sdk else ['langfuse', 'nemo-relay'])
    if len(sdks) != len(set(sdks)):
        parser.error('SDK profile cannot repeat an SDK')
    if args.sdk_coverage == 'smoke' and (
        args.mode != 'ci' or sdks != ['langfuse', 'nemo-relay']
        or os.environ.get('AIVISION_E2E_DATA_BACKEND') != 'oceanbase'
        or importlib.metadata.version('langfuse') != '4.16.0'
    ):
        parser.error('SDK smoke requires CI on OceanBase with pinned Langfuse 4.16.0 and NeMo Relay')
    selected = args.scenarios.split(',') if args.scenarios else scenarios(args.sdk_coverage)
    if not selected or any(s not in scenarios() for s in selected) or len(selected) != len(set(selected)):
        parser.error('Unknown, empty or repeated scenarios')
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out/'summary.json').exists():
        parser.error('Choose a fresh output directory; old results cannot satisfy this run')
    data_backend = None
    if args.mode == 'ci':
        try:
            data_backend = ci_backend(os.environ.get('AIVISION_E2E_DATA_BACKEND'))
        except ValueError as error:
            parser.error(str(error))
    if args.mode in ('ci', 'platform'):
        targets = json.loads(os.environ.get('AIVISION_E2E_TARGETS_JSON') or '{}')
        wanted = sdks
        if any(sdk not in targets for sdk in wanted):
            parser.error('Real-platform acceptance requires explicit AIVISION_E2E_TARGETS_JSON targets')
        if args.mode == 'ci':
            try:
                validate_ci_targets(data_backend, targets, sdks)
            except ValueError as error:
                parser.error(str(error))
    else:
        targets = {}
    frameworks = [args.framework] if args.framework else ['langchain', 'deepagents']
    matrix = [(f, s, case) for f in frameworks for s in sdks for case in selected]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(run_case, root, args.out, repo, *case, args.mode != 'collector', targets): case for case in matrix}
        for future in as_completed(pending):
            case = pending[future]
            try:
                results.append(future.result())
            except Exception as error:
                results.append({'case': '-'.join(case), 'execute_exit': 1, 'verify_exit': 1,
                                'source_verified': False, 'platform_verified': False,
                                'failures': [type(error).__name__]})
    summary = {'status': acceptance_status(results, len(matrix), args.mode != 'collector'),
               'mode': args.mode, 'expected_cases': len(matrix), 'executed_cases': len(results),
               'os': platform.system(), 'architecture': platform.machine(), 'python': platform.python_version(),
               'langfuse_version': importlib.metadata.version('langfuse'), 'paid_model_calls': 0,
               'data_backend': data_backend, 'sdks': sdks, 'sdk_coverage': args.sdk_coverage,
               'platform_boundary': 'real AI Vision HTTP APIs and declared OceanBase/seekdb deployment' if args.mode == 'ci' else args.mode,
               'cases': sorted(results, key=lambda r: r['case'])}
    (args.out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('status', 'mode', 'sdk_coverage', 'expected_cases',
                                             'executed_cases', 'os', 'langfuse_version')}))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
