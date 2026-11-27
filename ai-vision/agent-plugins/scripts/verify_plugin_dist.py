#!/usr/bin/env python3
"""Validate manifests, checksums, binary architecture and production packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import tarfile

PLATFORMS = {('linux-gnu', 'x64'), ('linux-gnu', 'arm64'), ('darwin', 'x64'), ('darwin', 'arm64')}


def binary_platform(path):
    with path.open('rb') as stream:
        header = stream.read(32)
    if header[:4] == b'\x7fELF' and header[4] == 2:
        arch = {62: 'x64', 183: 'arm64'}.get(int.from_bytes(header[18:20], 'little' if header[5] == 1 else 'big'))
        return 'linux-gnu', arch
    if header[:4] in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf'):
        cpu = struct.unpack('<I' if header[0] == 0xcf else '>I', header[4:8])[0]
        return 'darwin', {0x1000007: 'x64', 0x100000c: 'arm64'}.get(cpu)
    raise ValueError(f'{path}: unrecognized executable format')


def verify_glibc(path, maximum):
    """Inspect versioned ELF dependencies rather than trusting the build label."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', maximum):
        raise ValueError('Invalid maximum glibc version')
    output = subprocess.run(['readelf', '--version-info', '--wide', str(path)],
                            capture_output=True, text=True, check=True, timeout=30,
                            env={**os.environ, 'LC_ALL': 'C'}).stdout
    versions = set(re.findall(r'\bName: (GLIBC_\S+)', output))
    limit = tuple(map(int, maximum.split('.'))) + (0,)
    for version in sorted(versions):
        number = version.removeprefix('GLIBC_')
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', number) or tuple(map(int, number.split('.'))) > limit:
            raise ValueError(f'{path}: requires {version}, above the glibc {maximum} release baseline')


def verify_native(path, binary, target):
    system = {'Linux': 'linux-gnu', 'Darwin': 'darwin'}.get(platform.system())
    arch = {'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if target != (system, arch):
        raise ValueError(f'{path}: native execution requested on the wrong platform')
    result = subprocess.run([str(path.resolve()), '--version'], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=True, timeout=10)
    if not result.stdout.strip().startswith(binary + ' '):
        raise ValueError(f'{path}: native runtime check did not report the hook version')


def verify(dist, catalog, required, agents=None, require_privacy=False, max_glibc=None, run_native=False):
    for plugin in catalog:
        if agents and plugin['key'] not in agents:
            continue
        # Catalog entries that declare neither a `binary` (Rust hook) nor a
        # `package` (Node bundle) are agent-market metadata only (e.g. framework
        # onboarding such as langfuse / nvidia-nemo-relay). build_plugins.py
        # produces no dist artifact for them, so they must not be verified here.
        if not (plugin.get('binary') or plugin.get('package')):
            continue
        root = dist / plugin['key']
        if plugin.get('preview') and not agents and not root.exists():
            continue
        index = json.loads((root / 'latest.json').read_text())
        version = index['version']
        if Path(version).name != version or version in ('.', '..'):
            raise ValueError('Invalid artifact version')
        found = set()
        for item in index['downloads']:
            if require_privacy and set(item.get('collection_modes', [])) != {'full', 'privacy'}:
                raise ValueError(f'{plugin["key"]}: artifact is missing Full/Privacy capability metadata')
            target = item['platform'], item['arch']
            if target in found:
                raise ValueError(f'{plugin["key"]}: duplicate platform {target}')
            found.add(target)
            if Path(item['filename']).name != item['filename']:
                raise ValueError('Artifact must be a single filename')
            path = root / version / item['filename']
            if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
                raise ValueError(f'{path}: missing or invalid artifact')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item['sha256']:
                raise ValueError(f'{path}: SHA-256 mismatch')
            if plugin.get('binary'):
                if item['target_filename'] != plugin['binary'] or binary_platform(path) != target:
                    raise ValueError(f'{path}: executable does not match manifest platform/architecture')
                if max_glibc and target[0] == 'linux-gnu':
                    verify_glibc(path, max_glibc)
                if run_native and target in required:
                    verify_native(path, plugin['binary'], target)
            else:
                if target != ('all', 'all'):
                    raise ValueError(f'{path}: Node plugin must be universal')
                with tarfile.open(path, 'r:gz') as archive:
                    members = archive.getmembers()
                    for member in members:
                        relative = Path(member.name)
                        if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'package' or not (member.isfile() or member.isdir()):
                            raise ValueError(f'{path}: unsafe package member')
                    names = {m.name for m in members if m.isfile()}
                    if not {'package/package.json', 'package/dist/index.js'} <= names:
                        raise ValueError(f'{path}: incomplete production package')
                    manifest = json.load(archive.extractfile('package/package.json'))
                    if manifest['name'] != plugin.get('package_name', plugin['package']) or manifest['version'] != version:
                        raise ValueError(f'{path}: package identity mismatch')
            print(f'{plugin["key"]} {version}: {target[0]}/{target[1]} checksum and artifact OK')
        expected = required if plugin.get('binary') else {('all', 'all')}
        if not expected <= found:
            raise ValueError(f'{plugin["key"]}: missing release platforms {sorted(expected - found)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[1] / 'agents.json')
    parser.add_argument('--platform', choices=sorted('/'.join(p) for p in PLATFORMS))
    parser.add_argument('--agent', action='append')
    parser.add_argument('--require-privacy', action='store_true')
    parser.add_argument('--max-glibc', help='Reject Linux binaries requiring newer glibc symbols (needs readelf)')
    parser.add_argument('--run-native', action='store_true', help='Execute matching hooks with --version on this host')
    args = parser.parse_args()
    if args.run_native and not args.platform:
        parser.error('--run-native requires --platform')
    required = {tuple(args.platform.split('/'))} if args.platform else PLATFORMS
    try:
        catalog = json.loads(args.catalog.read_text())
        for plugin in catalog:
            if plugin.get('package'):
                # The install directory key can differ from the npm package's
                # scoped name (e.g. observ-pi versus @observ/pi).
                source = args.catalog.parent / plugin['source'] / 'package.json'
                plugin['package_name'] = json.loads(source.read_text())['name']
        verify(args.dist, catalog, required, args.agent, args.require_privacy, args.max_glibc, args.run_native)
    except (ValueError, OSError, KeyError, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Artifact verification failed: {exc}\n')


if __name__ == '__main__':
    main()
