#!/usr/bin/env python3
"""Exercise the frozen MiniMax candidate with runner-local OCI images and a packaged aiv."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
import tempfile

from seekdb_deploy_smoke import (
    CANDIDATE_PLATFORM, COMPONENTS, IMAGE_VARS, main as deploy_smoke,
)


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'ai-vision/agent-plugins/tests/native_agent_e2e/minimax_deployed.py'
sys.path.insert(0, str(ROOT / 'scripts/ci'))
from native_cli_archive import CLI_MEMBERS, CLI_MODULE, CLI_VERSION_SYMBOL
from release_assets import DATABASES
from release_plan import VERSION

BACKENDS = {'seekdb': 'seekdb', 'oceanbase-ce': 'oceanbase'}
OWNED = {'frontend', 'backend', 'gateway', 'agentlens'}
TAG = re.compile(r'[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def checked(command, *, timeout=90):
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{command[0]} {command[1]} exited with code {result.returncode}')
    return result.stdout.strip()


def validate_inputs(manifest):
    fields = {'source_sha', 'source_tree', 'release_version', 'platform', 'plugin_sha256',
              'cli_sha256', 'images', 'image_ids'}
    lock = read_json(ROOT / 'deploy/config/release-dependencies.json')
    upstream = {'signoz': lock['signoz']['image'], 'seekdb': DATABASES['seekdb'],
                'oceanbase-ce': DATABASES['oceanbase']}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest['platform'] != CANDIDATE_PLATFORM:
        raise ValueError('candidate requires complete Linux/amd64 input metadata')
    for key, pattern in [('source_sha', r'[0-9a-f]{40}'), ('source_tree', r'[0-9a-f]{40}'),
                         ('plugin_sha256', r'[0-9a-f]{64}'), ('cli_sha256', r'[0-9a-f]{64}')]:
        if not isinstance(manifest[key], str) or not re.fullmatch(pattern, manifest[key]):
            raise ValueError(f'invalid candidate {key}')
    version = manifest['release_version']
    if (not isinstance(version, str) or not VERSION.fullmatch(version)
            or version != (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
            or version.split('-', 1)[0] != lock['product_version']):
        raise ValueError('candidate release version differs from the source or dependency lock')
    for key in ('images', 'image_ids'):
        if not isinstance(manifest[key], dict) or set(manifest[key]) != OWNED | upstream.keys():
            raise ValueError(f'candidate {key} must identify all seven components')
    if any(not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value)
           for value in manifest['image_ids'].values()):
        raise ValueError('candidate Docker image config IDs must be SHA-256')
    images = manifest['images']
    if any(images[key] != pinned for key, pinned in upstream.items()):
        raise ValueError('candidate upstream images must match the existing release and database pins')
    if any(not isinstance(images[key], str) or not TAG.fullmatch(images[key]) for key in OWNED):
        raise ValueError('candidate service images require explicit local tags')
    if len(set(images.values())) != len(images):
        raise ValueError('candidate image references must be distinct')
    return manifest


def verify_source(manifest):
    if (checked(['git', 'rev-parse', 'HEAD']) != manifest['source_sha']
            or checked(['git', 'rev-parse', 'HEAD^{tree}']) != manifest['source_tree']
            or checked(['git', 'status', '--porcelain', '--untracked-files=no'])):
        raise ValueError('checkout differs from the frozen candidate source')


def verify_host(local_aiv):
    system, machine = platform.system(), platform.machine()
    if local_aiv:
        if (system, machine) != ('Darwin', 'arm64'):
            raise ValueError('--local-aiv is for an isolated macOS arm64 follow-on')
    elif system != 'Linux' or machine not in ('x86_64', 'amd64'):
        raise ValueError('candidate acceptance requires a native Linux x64 host')
    docker_host = os.environ.get('DOCKER_HOST')
    if not docker_host or os.environ.get('DOCKER_CONTEXT'):
        docker_host = checked(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
    if not docker_host.startswith('unix:///'):
        raise ValueError('candidate acceptance requires a runner-local Docker Unix socket')
    server = checked(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'])
    if server not in {'linux/x86_64', 'linux/amd64'} and not (local_aiv and server in {'linux/aarch64', 'linux/arm64'}):
        raise ValueError('Docker server platform does not match this acceptance lane')
    return server


def load_images(manifest, local_alias_file):
    images = dict(manifest['images'])
    aliases = read_json(local_alias_file) if local_alias_file else {}
    if (not isinstance(aliases, dict) or set(aliases) - {'signoz', *BACKENDS}
            or any(not isinstance(alias, str) or not TAG.fullmatch(alias) for alias in aliases.values())):
        raise ValueError('local aliases may only select tagged copies of the three upstream images')
    images.update(aliases)
    for key, ref in images.items():
        result = checked(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}} {{.Id}}', ref])
        if result != f'{CANDIDATE_PLATFORM} {manifest["image_ids"][key]}':
            raise ValueError('preloaded Docker image platform or config ID differs for ' + key)
    return images


def verify_cli_build(binary, manifest, os_name, arch):
    rows = checked(['go', 'version', '-m', str(binary)]).splitlines()[1:]
    identity = [line.split()[1] for line in rows if line.lstrip().startswith(('path\t', 'mod\t'))]
    settings = dict(line.strip().split('\t', 1)[1].split('=', 1)
                    for line in rows if line.lstrip().startswith('build\t'))
    version = manifest['release_version']
    flags = settings.get('-ldflags', '').strip('"')
    expected = {'GOOS': os_name, 'GOARCH': arch, 'CGO_ENABLED': '0',
                'vcs.revision': manifest['source_sha'], 'vcs.modified': 'false', '-tags': ''}
    if (identity != [CLI_MODULE, CLI_MODULE] or flags != f'-X {CLI_VERSION_SYMBOL}={version}'
            or any(settings.get(key, '') != value for key, value in expected.items())):
        raise ValueError('aiv build metadata differs from the frozen source-compatible candidate')


def prepare_cli(path, private, manifest, local_aiv=None):
    if digest(path) != manifest['cli_sha256']:
        raise ValueError('candidate aiv archive SHA-256 differs')
    candidate = private / 'aiv'
    with tarfile.open(path, 'r:gz') as package:
        members = package.getmembers()
        if (len(members) != len(CLI_MEMBERS) or {item.name for item in members} != CLI_MEMBERS
                or any(not item.isfile() for item in members)):
            raise ValueError('candidate aiv archive has an unexpected file inventory')
        member = package.getmember('aiv')
        if not 0 < member.size < 150 * 1024**2 or member.mode & 0o111 == 0:
            raise ValueError('candidate aiv archive requires an executable payload')
        candidate.write_bytes(package.extractfile(member).read())
    candidate.chmod(0o700)
    verify_cli_build(candidate, manifest, os_name='linux', arch='amd64')
    if local_aiv:
        executable = local_aiv.resolve(strict=True)
        verify_cli_build(executable, manifest, os_name='darwin', arch='arm64')
    else:
        executable = candidate
    if checked([str(executable), '--version']) != 'aiv ' + manifest['release_version']:
        raise ValueError('running aiv version differs from the candidate version')
    return executable


def verify_plugin(dist, manifest):
    root = dist / 'minimax-code'
    index = read_json(root / 'latest.json')
    version = index['version']
    download, = index['downloads']
    filename = download['filename']
    expected = manifest['plugin_sha256']
    if (not re.fullmatch(r'[0-9][0-9A-Za-z.+-]*', version)
            or filename != f'observ-minimax-code-{version}.tgz'
            or (download['platform'], download['arch']) != ('all', 'all')
            or download['sha256'] != expected
            or digest(root / version / filename) != expected):
        raise ValueError('MiniMax plugin artifact does not match the frozen candidate')
    artifact_path = f'/agent-plugins/dist/minimax-code/{version}/{filename}'
    result = checked(['docker', 'run', '--rm', '--network', 'none', '--pull=never', '--platform',
                      CANDIDATE_PLATFORM, '--entrypoint', 'sha256sum', manifest['image_ids']['backend'], artifact_path])
    if result.split() != [expected, artifact_path]:
        raise ValueError('backend image does not contain the expected MiniMax plugin artifact')


def live_minimax(phase, smoke, trace, cli, *, mcode, plugin_sha, live_key_file):
    state = smoke.runtime / 'minimax'
    account = state / 'account.json'
    if phase == 'initial':
        state.mkdir(mode=0o700)
        with os.fdopen(os.open(account, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
            json.dump({'email': trace.name + '@example.invalid', 'space_id': trace.space_id,
                       'password': smoke.values['SIGNOZ_USER_ROOT_PASSWORD']}, out)
    command = [sys.executable, str(HARNESS), '--base-url', trace.origin, '--aiv', str(cli),
               '--mcode', str(mcode), '--phase', phase, '--state-dir', str(state), '--account-file', str(account)]
    if phase == 'initial':
        command.extend(('--artifact-sha256', plugin_sha))
    if live_key_file:
        smoke.env['AIVISION_MINIMAX_LIVE_KEY_FILE'] = live_key_file
    try:
        result = smoke.run('minimax-' + phase, command, timeout=600)
    finally:
        smoke.env.pop('AIVISION_MINIMAX_LIVE_KEY_FILE', None)
    receipt = json.loads(result.stdout)
    expected = {'status': 'passed', 'phase': phase, 'artifact_sha256': plugin_sha, 'trace_count': 4,
                'model_mode': 'openai-byok' if live_key_file else 'loopback-fixture'}
    if (not isinstance(receipt, dict) or set(receipt) != set(expected) | {'traces'}
            or any(receipt[key] != value for key, value in expected.items())
            or not isinstance(receipt['traces'], list) or len(receipt['traces']) != 4):
        raise RuntimeError('deployed MiniMax client acceptance did not pass: ' + phase)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--aiv-archive', type=Path, required=True)
    parser.add_argument('--plugin-dist', type=Path, required=True)
    parser.add_argument('--mcode', type=Path, required=True)
    parser.add_argument('--runtime-base', type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument('--out', type=Path, required=True, help='new or empty directory for sanitized receipts')
    parser.add_argument('--local-aiv', type=Path, help='native macOS arm64 aiv for an isolated local model follow-on')
    parser.add_argument('--local-upstream-aliases', type=Path, help='local tags for the exact upstream image configs')
    args = parser.parse_args(argv)
    if args.local_upstream_aliases and not args.local_aiv:
        parser.error('--local-upstream-aliases requires --local-aiv')
    live_key_file = os.environ.get('AIVISION_MINIMAX_LIVE_KEY_FILE')
    if live_key_file and (not args.local_aiv or os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS')):
        parser.error('live model credentials are only available to an isolated local follow-on')
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        if any(args.out.iterdir()):
            raise ValueError('--out must be empty')
        mcode = args.mcode.absolute()
        if not mcode.is_file() or not os.access(mcode, os.X_OK):
            raise ValueError('--mcode requires an executable')
        manifest = validate_inputs(read_json(args.inputs))
        verify_source(manifest)
        server = verify_host(args.local_aiv)
        images = load_images(manifest, args.local_upstream_aliases)
        verify_plugin(args.plugin_dist, manifest)
        with tempfile.TemporaryDirectory(prefix='aiv-minimax-oci-', dir=args.runtime_base) as runtime:
            private = Path(runtime).resolve()
            cli = prepare_cli(args.aiv_archive, private, manifest, args.local_aiv)

            def check(phase, smoke, trace, cli):
                return live_minimax(phase, smoke, trace, cli, mcode=mcode,
                                    plugin_sha=manifest['plugin_sha256'], live_key_file=live_key_file)

            for key, backend in BACKENDS.items():
                log_dir = private / (key + '-logs')
                refs = {'database': images[key], **{name: images[name] for name in IMAGE_VARS}}
                ids = dict(zip(COMPONENTS, (manifest['image_ids'][name] for name in (key, *IMAGE_VARS))))
                status = deploy_smoke(['--backend', backend, '--aiv', str(cli), '--out', str(log_dir),
                                       '--runtime-base', str(private), '--require-native-memory'],
                                      local_images=(refs, ids), live_check=check)
                receipt = read_json(log_dir / 'summary.json')
                receipt.update(candidate_inputs=manifest, docker_server_platform=server,
                               qualification='isolated-macos-follow-on' if args.local_aiv else 'native-linux-amd64-local-oci',
                               executing_cli_sha256=digest(cli))
                if status:
                    for phase in ('initial', 'recast'):
                        log = log_dir / f'minimax-{phase}.log'
                        if log.is_file():
                            reason = next((line for line in log.read_text(encoding='utf-8').splitlines()
                                           if line.startswith('MiniMax qualification failed')), None)
                            if reason:
                                receipt['minimax_failure'] = reason
                                print(reason, file=sys.stderr)
                                break
                (args.out / (key + '.json')).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
                if status:
                    return status
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f'MiniMax OCI candidate acceptance failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
