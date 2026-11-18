"""Sequential, local-only diagnostics acceptance inside the owned platform lifetime.

The current docker_platform supplies the fake source/platform gate and owns all
services. This callback adds optional live source, replay, Quick, calibration,
retest and externally supplied real screenshot evidence before its finally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import uuid
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from calibrate import _outside_git, provider_lease
from platform_client import PlatformClient, validate_diagnosis
from quick import QuickRunner, canonical_hash


class LocalError(RuntimeError):
    pass


class SharedProviderBudget:
    """Route all proxy attempts to one lease, with per-case limits in calibration."""
    def __init__(self, lease):
        self.lease = lease
        self.calibration = None

    def reserve(self, stop=None):
        current = self.calibration or self.lease
        return current.reserve(stop)

    @contextmanager
    def calibrated(self, scoped):
        _require(self.calibration is None, 'Calibration budget already active')
        self.calibration = scoped
        try:
            yield
        finally:
            self.calibration = None


def _require(ok, message):
    if not ok:
        raise LocalError(message)


def _private_write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def _capture_ids(directory, case):
    source = json.loads((directory / case / 'source.json').read_text())
    _require(source.get('source', {}).get('verified') is True,
        'Actual source capture failed')
    trace_ids = sorted({span.get('trace_id') for export in source.get('exports', [])
        for span in export.get('spans', []) if isinstance(span.get('trace_id'), str)})
    _require(1 <= len(trace_ids) <= 10 and all(re.fullmatch(r'[0-9a-f]{32}', value)
        for value in trace_ids), 'Source capture lacks complete trace IDs')
    return trace_ids


def _read_stage_summary(path):
    summary = json.loads(path.read_text())
    _require(summary.get('verified') is True and summary.get('completed') == summary.get('expected'),
        'Native capture stage did not complete all cases')
    return {'stage': summary.get('stage'), 'completed': summary['completed'],
        'provider_attempts': summary.get('provider_attempts')}


def _require_ci_quick_result(client, result, trace_ids):
    """The fixed read-only smoke needs evaluated results, not just a done Task."""
    snapshot = result['snapshot']
    counts = snapshot['counts']
    _require(counts.get('known') == len(trace_ids)
        and all(counts.get(key) == 0 for key in ('unknown', 'partial', 'failed', 'skipped'))
        and len(snapshot['record_uuids']) == len(trace_ids),
        'CI Quick requires evaluated tool_success results for every source trace')
    task = client.scoped('/tasks/' + result['receipt']['task_uuid'], agent=False)
    records = [client.scoped('/records/' + record_id, agent=False)
        for record_id in snapshot['record_uuids']]
    _require(canonical_hash(task) == snapshot['hashes']['task']
        and canonical_hash(sorted(records, key=lambda row: row['uuid']))
            == snapshot['hashes']['records'],
        'CI Quick evidence changed after its frozen snapshot')
    stats = task['stats']
    definitions = [row for row in stats['diagnoser_definitions'].values()
        if row.get('builtin_key', row.get('key')) == 'tool_success']
    _require(stats.get('source_complete') is True and len(definitions) == 1,
        'CI Quick requires complete source and the selected frozen tool_success rule')
    proof = validate_diagnosis(task, records, {'tool_success': {
            'uuid': definitions[0]['uuid'],
            'spec': {'metrics': [{'name': 'tool_success_rate'}]}}},
        source_trace_ids=set(trace_ids), source_span_ids=set(),
        expected={'tool_success': 'good'},
        expected_evidence={'tool_success': dict.fromkeys(trace_ids, {
            'counts': {'success': 1, 'failure': 0, 'unknown': 0}})})
    _require(proof['verified'], 'CI Quick did not evaluate the expected successful read: '
        + ', '.join(proof['failures']))
    result['rule_acceptance'] = proof


def ci_owned_fake_after_acceptance(**context):
    """Fixed CI callback: this run's fake source -> owned Quick Rule API."""
    _require(context['result'].get('verified') is True
        and context['result'].get('expected_cases') == 34
        and context['result'].get('executed_cases') == 34,
        'Exact 34-case fake platform acceptance did not complete')
    targets = context['targets']
    _require(set(targets) == {'codex', 'dsh02'},
        'CI Quick requires exactly the current owned targets')
    target = targets['codex']
    parsed = urlsplit(target.get('endpoint', ''))
    _require(parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        and not parsed.username and not parsed.password and not parsed.query,
        'CI Quick endpoint must be owned loopback')
    ids = _capture_ids(Path(context['out']) / 'cases', 'codex-read-only')
    from run_suite import native_sessions
    current_file = Path(context['runtime']) / 'ci-quick-targets.json'
    _private_write(current_file, targets)
    try:
        with PlatformClient(target['endpoint'], target=target,
                dashboard_sessions=native_sessions.DashboardSessions()) as client:
            try:
                options = client.scoped('/quick-runs/options')
                scene = next((row for row in options.get('scenes', [])
                    if row.get('scene_key') == 'quick_runtime'), None)
                _require(scene and 'tool_success' in scene.get('rule_keys', []),
                    'Current owned Quick Rule catalog unavailable')
                result = QuickRunner(client).run(ids, ['tool_success'], scene='quick_runtime')
                _require_ci_quick_result(client, result, ids)
            finally:
                _publish_target(targets, 'codex', client,
                    context['register_private_targets'], current_file)
    finally:
        current_file.unlink(missing_ok=True)
    return {'verified': True, 'mode': 'ci_owned_fake', 'source_case': 'codex-read-only',
        'quick': result, 'screenshots': 'not_requested', 'provider': 'fake_only'}


def ci_owned_main(argv=None, *, platform_main=None):
    """Internal CI entry; docker_platform must opt in with its ci_owned guard."""
    parser = argparse.ArgumentParser(description='Owned fake platform and actual Quick API gate')
    parser.add_argument('--ci-owned-fake', action='store_true', required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--codex-image', required=True)
    parser.add_argument('--hook-binary', type=Path, required=True)
    parser.add_argument('--dsh-node', type=Path, required=True)
    parser.add_argument('--dsh02', type=Path, required=True)
    args = parser.parse_args(argv)
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('Owned fake CI entry requires GitHub Actions')
    if os.environ.get('NVIDIA_API_KEY') or os.environ.get('NVIDIA_PROXY'):
        parser.error('Owned CI fake gate refuses real provider configuration')
    repo, out = args.repo.resolve(), _outside_git(args.out)
    if out.exists():
        parser.error('Use a fresh private CI output outside Git')
    if platform_main is None:
        from docker_platform import main as platform_main
    runtime = ['--repo', str(repo), '--out', str(out),
        '--codex-image', args.codex_image, '--hook-binary', str(args.hook_binary),
        '--dsh-node', str(args.dsh_node), '--dsh02', str(args.dsh02), '--extended']
    return platform_main(runtime, ci_owned=True)


def _regular_digest(path):
    _require(not path.is_symlink(), 'Hook input must be a regular file, not a symlink')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 256 * 1024 * 1024,
            'Hook input must be a bounded regular file')
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()


def _mounted_hook(source, artifacts):
    """Copy only the vetted hook file into this run's Docker-visible artifact mount."""
    source, artifacts = Path(source), Path(artifacts)
    _require(source.is_absolute() and artifacts.is_dir() and not artifacts.is_symlink(),
        'Explicit hook and owned artifact directory required')
    expected = _regular_digest(source)
    inputs = artifacts / 'runtime-inputs'
    if not inputs.exists():
        inputs.mkdir(mode=0o700)
    _require(inputs.is_dir() and not inputs.is_symlink()
        and (os.name == 'nt' or stat.S_IMODE(inputs.stat().st_mode) == 0o700),
        'Hook mount directory must be owned and private')
    mounted = inputs / 'cdxhooks'
    if not mounted.exists() and not mounted.is_symlink():
        descriptor = os.open(mounted, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, 'O_NOFOLLOW', 0), 0o700)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                source_descriptor = os.open(source,
                    os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(source_descriptor, 'rb') as original:
                    _require(stat.S_ISREG(os.fstat(original.fileno()).st_mode),
                        'Hook source changed during copy')
                    shutil.copyfileobj(original, target, length=1024 * 1024)
            mounted.chmod(0o700)
        except BaseException:
            mounted.unlink(missing_ok=True)
            raise
    _require(not mounted.is_symlink() and (os.name == 'nt'
        or stat.S_IMODE(mounted.stat().st_mode) == 0o700)
        and mounted.resolve().is_relative_to(artifacts.resolve())
        and _regular_digest(mounted) == expected,
        'Mounted hook differs from the explicit vetted input')
    return mounted


def _run_capture(config, name, *, repo, out, targets_file, lease, runtime):
    from run_suite import main as run_source
    stage = config[name]
    directory = out / name
    mounted_hook = _mounted_hook(runtime['hook_binary'], out)
    args = ['--repo', str(repo), '--out', str(directory), '--artifacts', str(out),
        '--targets', str(targets_file), '--export-current-targets',
        str(out / (name + '-targets.json')), '--capture-only', '--mode', 'nvidia',
        '--model', stage['model'], '--apps', stage['app'], '--scenarios', *stage['scenarios'],
        '--codex-image', config['codex_image'], '--hook-binary', str(mounted_hook),
        '--dsh-node', runtime['dsh_node'], '--dsh02', runtime['dsh02']]
    prior_umask = os.umask(0o077)
    try:
        code = run_source(args, budget=lease)
    finally:
        os.umask(prior_umask)
    _require(code == 0, name + ' source runner failed')
    summary = _read_stage_summary(directory / 'summary.json')
    return summary, out / (name + '-targets.json')


def _update_targets(targets, current_path, register):
    updated = json.loads(current_path.read_text())
    _require(set(updated) == set(targets), 'Current target ownership changed')
    for app, target in updated.items():
        _owned_target(targets[app], target)
    register(updated)
    targets.clear()
    targets.update(updated)


def _publish_target(targets, app, client, register, target_path):
    """Publish the actual rotated session before another stage can use it."""
    latest = deepcopy(client.target)
    _owned_target(targets[app], latest)
    register({app: latest})
    targets[app] = latest
    pending = target_path.with_name(target_path.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        _private_write(pending, targets)
        os.replace(pending, target_path)
    finally:
        pending.unlink(missing_ok=True)


def _owned_target(before, after):
    _require(isinstance(after, dict) and all(after.get(key) == before.get(key)
        for key in ('endpoint', 'agent_uuid', 'space_id', 'isolation_space_id')),
        'Rotated target changed owner')
    parsed = urlsplit(after.get('endpoint', ''))
    _require(parsed.scheme == 'http'
        and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        and not parsed.username and not parsed.password and not parsed.query
        and not parsed.fragment, 'Rotated target left owned loopback endpoint')


def _provider_host(value):
    _require(value in {'host.orb.internal', 'host.docker.internal', '127.0.0.1'},
        'Explicit owned proxy host required')
    return value


def _quick_connection(client, selection, provider):
    from judge import create_connection
    _require('model_connection_uuid' not in selection,
        'Direct model connection bypasses the shared provider lease')
    core = set(selection['builtin_keys']) & {'task_completion', 'report_fidelity', 'answer_grounding'}
    if not core:
        _require('provider_model' not in selection, 'Rule-only Quick must omit a model')
        return None
    _require(len(core) == 1 and isinstance(selection.get('provider_model'), str)
        and selection['provider_model'].strip(), 'Pinned Core model required')
    endpoint = provider.url.replace('127.0.0.1', _provider_host(selection.get('provider_host')))
    connection = create_connection(client, name='Owned Quick Core ' + uuid.uuid4().hex[:12],
        endpoint=endpoint, model_name=selection['provider_model'],
        api_key='diagnostic-loopback-placeholder')
    return connection['uuid']


def _calibrate_owned(stage, *, targets, register, target_path, out, lease,
        provider, proxy_budget):
    """Existing Core Trial executor with a caller-owned, observable API session."""
    from calibrate import AttemptBudget, read_frozen_config, run_calibration
    from core_trial import CoreTrialExecutor, core_binding
    from corpus import load_package
    from judge import create_connection
    from run_suite import native_sessions

    package = load_package(Path(stage['corpus']))
    frozen = read_frozen_config(Path(stage['frozen_config']))
    adapter = frozen.get('adapter')
    _require(isinstance(adapter, dict) and set(adapter) ==
        {'kind', 'app', 'provider_host', 'stability_cases'}
        and adapter['kind'] == 'core_trial_v1'
        and adapter['app'] in {'codex', 'dsh02'}
        and isinstance(adapter['stability_cases'], list),
        'Explicit current Core Trial adapter required')
    _provider_host(adapter['provider_host'])
    for metric_key, template in frozen['templates'].items():
        core_binding(metric_key, template)
    _require(all(label['metric_key'] in frozen['templates']
        for label in package['labels']['labels']), 'Frozen Core labels incomplete')
    app = adapter['app']
    target = targets[app]
    sessions = native_sessions.DashboardSessions()
    with PlatformClient(target['endpoint'], target=target,
            dashboard_sessions=sessions) as client:
        try:
            def factory(provider):
                endpoint = provider.url.replace('127.0.0.1', adapter['provider_host'])
                connection = create_connection(client,
                    name='Frozen Core corpus ' + uuid.uuid4().hex[:12],
                    endpoint=endpoint, model_name=frozen['model'],
                    api_key='diagnostic-loopback-placeholder')
                return CoreTrialExecutor(client, connection_uuid=connection['uuid'],
                    provider=provider)
            scoped = AttemptBudget(lease)
            with proxy_budget.calibrated(scoped):
                report = run_calibration(package, execute=factory(provider),
                    budget=lease, frozen=frozen, mode='real',
                    provider_url=provider.url, provider_budget=scoped,
                    repeats=stage.get('repeats', 3),
                    stability_cases=adapter['stability_cases'])
            output = out / 'calibration'
            output.mkdir(mode=0o700)
            _private_write(output / 'report.json', report)
            _require(report.get('passed') is True, 'Actual Core Trial calibration failed')
            return {'verified': True,
                'human_count': report.get('summary', {}).get('labelers', {}).get('human'),
                'agent_count': report.get('summary', {}).get('labelers', {}).get('agent'),
                'disputed_count': report.get('summary', {}).get('disputed'),
                'usage_unknown': report.get('summary', {}).get('usage_unknown')}
        finally:
            _publish_target(targets, app, client, register, target_path)


def after_acceptance(config, *, screenshot_callback=None, **context):
    """Run while docker_platform still owns live targets and services."""
    repo, out, runtime = (Path(context[key]) for key in ('repo', 'out', 'runtime'))
    targets = context['targets']
    register = context['register_private_targets']
    _require(context['result'].get('verified') is True,
        'Fake platform acceptance must complete first')
    evidence = {'verified': False, 'stages': {'fake': {'verified': True,
        'expected_cases': context['result']['expected_cases']}},
        'manual': {'two_human_reviews': 'pending', 'novice_trials': 'pending'}}
    targets_file = runtime / 'validate-local-targets.json'
    _private_write(targets_file, targets)
    owned_target_paths = [targets_file]
    key = os.environ.get('NVIDIA_API_KEY')
    quick_core = bool(set((config.get('quick') or {}).get('builtin_keys', [])) &
        {'task_completion', 'report_fidelity', 'answer_grounding'})
    needs_provider = quick_core or any(name in config for name in
        ('live', 'after_live', 'calibration'))
    _require(not needs_provider or (key and not os.environ.get('GITHUB_ACTIONS')),
        'Live stages require explicitly supplied local NVIDIA_API_KEY')
    try:
        def stages(lease):
            nonlocal targets_file
            if 'live' in config:
                latest = out / 'live-targets.json'
                try:
                    summary, _ = _run_capture(config, 'live', repo=repo, out=out,
                        targets_file=targets_file, lease=lease, runtime=config['runtime'])
                    evidence['stages']['live'] = {'verified': True, **summary}
                finally:
                    if latest.is_file():
                        _update_targets(targets, latest, register)
                        targets_file = latest
                        owned_target_paths.append(latest)
            if 'replay' in config:
                from credentials import RenewingIngestHeaders
                from replay import authenticated_replay_scope, replay_package
                from run_suite import native_sessions
                stage = config['replay']
                path = out / 'replay'
                app = stage['app']
                target = targets[app]
                sessions = native_sessions.DashboardSessions()
                with PlatformClient(target['endpoint'], target=target,
                        dashboard_sessions=sessions) as client:
                    try:
                        result = replay_package(Path(stage['corpus']), target=target,
                            platform=client, out=path,
                            headers_factory=RenewingIngestHeaders(target,
                                native_sessions.refresh_ingest),
                            authenticated_scope=authenticated_replay_scope(client, target))
                        _require(result.get('verified') is True,
                            'Replay source admission unverified')
                    finally:
                        _publish_target(targets, app, client, register, targets_file)
                evidence['stages']['replay'] = {'verified': True, 'stage': 'source_only'}
            quick = None
            from calibrate import NvidiaProvider
            selection = config.get('quick') or {}
            timeout = selection.get('provider_timeout_sec', 7200)
            _require(type(timeout) in (int, float) and math.isfinite(timeout)
                and 0 < timeout <= 86400, 'Finite owned Quick proxy lifetime required')
            proxy_needed = quick_core or 'calibration' in config
            proxy_budget = SharedProviderBudget(lease) if proxy_needed else None
            provider_context = (NvidiaProvider(key, proxy_budget,
                proxy=selection.get('upstream_proxy'), timeout=timeout)
                if proxy_needed else nullcontext(None))
            with provider_context as provider:
                if 'quick' in config:
                    from run_suite import native_sessions
                    _require('live' in config and selection['app'] == config['live']['app'],
                        'Quick requires matching actual live source capture')
                    ids = _capture_ids(out / 'live', selection['case'])
                    app = selection['app']
                    target = targets[app]
                    with PlatformClient(target['endpoint'], target=target,
                            dashboard_sessions=native_sessions.DashboardSessions()) as client:
                        try:
                            model = _quick_connection(client, selection, provider)
                            quick = QuickRunner(client).run(ids, selection['builtin_keys'],
                                scene=selection['scene_key'], model=model,
                                timeout=selection.get('task_timeout_sec', 1100))
                        finally:
                            _publish_target(targets, app, client, register, targets_file)
                    evidence['stages']['quick'] = {'verified': True, **quick}
                if 'calibration' in config:
                    _require(provider is not None,
                        'Core calibration requires the shared owned Quick proxy')
                    evidence['stages']['calibration'] = _calibrate_owned(
                        config['calibration'], targets=targets, register=register,
                        target_path=targets_file, out=out, lease=lease,
                        provider=provider, proxy_budget=proxy_budget)
                if 'after_live' in config:
                    latest = out / 'after_live-targets.json'
                    try:
                        summary, _ = _run_capture(config, 'after_live', repo=repo, out=out,
                            targets_file=targets_file, lease=lease,
                            runtime=config['runtime'])
                        evidence['stages']['after_live'] = {'verified': True, **summary}
                    finally:
                        if latest.is_file():
                            _update_targets(targets, latest, register)
                            targets_file = latest
                            owned_target_paths.append(latest)
                if 'retest' in config:
                    from run_suite import native_sessions
                    stage = config['retest']
                    _require(quick is not None and 'after_live' in config,
                        'Retest requires original Quick and new live source capture')
                    _require(stage['app'] == config['after_live']['app']
                        and stage['app'] == selection['app'], 'Retest source app changed')
                    ids = _capture_ids(out / 'after_live', stage['case'])
                    app = stage['app']
                    target = targets[app]
                    with PlatformClient(target['endpoint'], target=target,
                            dashboard_sessions=native_sessions.DashboardSessions()) as client:
                        try:
                            retest = QuickRunner(client).retest(quick['receipt'], ids,
                                verification=stage['independent_verification'])
                        finally:
                            _publish_target(targets, app, client, register, targets_file)
                    evidence['stages']['retest'] = {'verified': True, **retest}
                if provider is not None:
                    evidence['stages']['quick_proxy'] = {'verified': True,
                        'attempts_observed': len(provider.requests), 'budget_attempts': lease.attempts}
            if config.get('screenshots_required'):
                _require(screenshot_callback is not None,
                    'Actual screenshot callback is required for UI proof')
                screenshots = screenshot_callback(targets=targets, out=out,
                    completed=deepcopy(evidence['stages']))
                _require(isinstance(screenshots, dict) and screenshots.get('verified') is True
                    and isinstance(screenshots.get('artifacts'), list)
                    and screenshots['artifacts'] and all(Path(path).is_file()
                    for path in screenshots['artifacts']),
                    'Actual screenshot artifacts missing')
                evidence['stages']['screenshots'] = screenshots
        if needs_provider:
            with provider_lease(key) as lease:
                stages(lease)
        else:
            stages(None)
        evidence['verified'] = True
        return evidence
    finally:
        register(targets)
        for path in owned_target_paths:
            path.unlink(missing_ok=True)


def main(argv=None, *, screenshot_callback=None, platform_main=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True,
        help='Private local JSON; explicit runtimes, images and stage selections')
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    out = _outside_git(args.out)
    config_path = _outside_git(args.config)
    if (out.exists() or not config_path.is_file()
            or config_path.stat().st_mode & 0o077
            or config_path.parent.stat().st_mode & 0o077):
        parser.error('Require a new private output and 0600 config in a 0700 directory outside Git')
    config = json.loads(config_path.read_text())
    _require(isinstance(config, dict) and isinstance(config.get('runtime'), dict)
        and isinstance(config.get('codex_image'), str), 'Explicit runtime and image required')
    for stage_name in ('replay', 'calibration'):
        if stage_name in config:
            _outside_git(Path(config[stage_name]['corpus']))
    if 'calibration' in config:
        _outside_git(Path(config['calibration']['frozen_config']))
    if config.get('screenshots_required') and screenshot_callback is None:
        parser.error('Actual screenshot callback required by private config')
    if platform_main is None:
        from docker_platform import main as platform_main
    runtime = config['runtime']
    argv = ['--repo', str(repo), '--out', str(out), '--codex-image', config['codex_image'],
        '--hook-binary', runtime['hook_binary'], '--dsh-node', runtime['dsh_node'],
        '--dsh02', runtime['dsh02'], '--extended']
    if config.get('signoz_source_archive'):
        argv.extend(['--signoz-source-archive', config['signoz_source_archive']])
    code = platform_main(argv, after_acceptance=lambda **kwargs: after_acceptance(
        config, screenshot_callback=screenshot_callback, **kwargs))
    return code


if __name__ == '__main__':
    raise SystemExit(ci_owned_main() if '--ci-owned-fake' in sys.argv else main())
