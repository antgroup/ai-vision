#!/usr/bin/env python3
r"""Build a native Agent installer without building an image or installing tools.

Run inside the target Linux ABI environment with CPython 3.11.14, Nuitka 4.3.1,
GCC and patchelf already installed. Example (paths are builder-local):

  python scripts/build/build_native_onboarding.py \
    --compiler-python /opt/compiler/bin/python --target linux-gnu-arm64 \
    --source-root ai-vision/agent-plugins/scripts \
    --work-dir /tmp/onboarding-build --output-dir /tmp/onboarding-artifacts \
    --dependency-root '/usr/local/lib=CPython 3.11.14 standard library' \
    --dependency-root '/usr/lib=Debian Bookworm upstream native libraries'

Outputs <output>/<target>/connect.tar.gz with exactly connect/connect + its
standalone dependencies. Compiler report, ownership hashes, source audit and
build provenance stay outside the archive. Layout and tar metadata are stable;
this does not claim bit-reproducible compiler output across different builders.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from xml.etree import ElementTree as ET

from native_artifacts import audit_artifact, make_manifest

PYTHON_VERSION = (3, 11, 14)
NUITKA_VERSION = '4.3.1'
OWNED_MODULES = {'__main__': 'connect', 'native_config': 'native_config.py', 'hook_runtime': 'hook_runtime.py'}
TARGETS = {'linux-gnu-arm64': ('arm64', 183), 'linux-gnu-x64': ('x64', 62)}


def _relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value:
        raise ValueError('noncanonical compiler destination: ' + value)
    return value


def _expanded(value, variables):
    for name, replacement in variables.items():
        value = value.replace('${' + name + '}', str(replacement))
    if '${' in value or not Path(value).is_absolute():
        raise ValueError('unresolved compiler source path: ' + value)
    return Path(value).resolve()


def dependency_destinations(report, source_root, allowed_dependencies, variables):
    """Derive exact output ownership from the pinned compiler's XML report."""
    source_root = Path(source_root).resolve()
    roots = {}
    for root, provenance in allowed_dependencies.items():
        path = Path(root)
        if not path.is_absolute() or path.resolve() == Path('/') or not provenance.strip():
            raise ValueError('dependencies need bounded absolute roots and provenance')
        roots[path.resolve()] = provenance
    if not roots:
        raise ValueError('explicit permitted upstream dependency roots are required')

    def upstream(source):
        if source.is_relative_to(source_root):
            raise ValueError('owned code was copied as runtime data/dependency: ' + str(source))
        for root in sorted(roots, key=lambda path: len(path.parts), reverse=True):
            if source.is_relative_to(root):
                return roots[root] + '; compiler source=' + str(source)
        raise ValueError('compiler input has no permitted upstream provenance: ' + str(source))

    document = ET.parse(report).getroot()
    if (document.get('nuitka_version') != NUITKA_VERSION or document.get('mode') != 'standalone'
            or document.get('completion') != 'yes'):
        raise ValueError('expected a completed pinned Nuitka standalone compilation report')
    compiled = set()
    for module in document.findall('module'):
        if not module.get('source_path'):
            continue
        source = _expanded(module.get('source_path'), variables)
        name = module.get('name')
        if name in OWNED_MODULES:
            if source != source_root / OWNED_MODULES[name] or module.get('kind') not in {'PythonMainModule', 'CompiledPythonModule'}:
                raise ValueError('owned module must be compiled into the installer: ' + name)
            compiled.add(name)
        else:
            upstream(source)
    if compiled != set(OWNED_MODULES):
        raise ValueError('all owned installer/helper modules must be compiled')
    destinations = {}
    for tag in ('included_extension', 'included_dll', 'data_file'):
        for item in document.findall(tag):
            if item.get('ignored') == 'yes':
                continue
            destination = _relative(item.get('dest_path', ''))
            if destination == 'connect' or destination in destinations:
                raise ValueError('duplicate or reserved compiler destination: ' + destination)
            destinations[destination] = upstream(_expanded(item.get('source_path', ''), variables))
    return destinations


def verify_elf_target(binary, target):
    header = Path(binary).read_bytes()[:64]
    if (target not in TARGETS or len(header) < 20 or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != TARGETS[target][1]):
        raise ValueError('native executable architecture does not match ' + target)


def package_archive(bundle, destination):
    """Write fixed metadata and canonical regular-file entries, with no links."""
    bundle = Path(bundle)
    entries = [(PurePosixPath('connect'), bundle), *[(PurePosixPath('connect') / path.relative_to(bundle).as_posix(), path)
               for path in sorted(bundle.rglob('*'))]]
    with Path(destination).open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, path in entries:
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise ValueError('archive requires materialized regular files: ' + str(path))
                    info = tarfile.TarInfo(str(name))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    info.mode = 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
                    if path.is_dir():
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
                    else:
                        info.size = path.stat().st_size
                        with path.open('rb') as content:
                            archive.addfile(info, content)


def _input_snapshot(source):
    paths = {name: source / filename for name, filename in OWNED_MODULES.items()}
    paths.update({'builder': Path(__file__).resolve(),
                  'source_audit': Path(__file__).resolve().with_name('native_artifacts.py')})
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


def _check_input_snapshot(source, initial):
    try:
        current = _input_snapshot(source)
    except OSError as error:
        raise ValueError('build inputs changed during compilation: ' + str(error)) from error
    if current != initial:
        changed = sorted(name for name in initial if initial[name] != current.get(name))
        raise ValueError('build inputs changed during compilation: ' + ', '.join(changed))


def _build_paths(args):
    source, work, output_root = args.source_root.resolve(), args.work_dir.resolve(), args.output_dir.resolve()
    for label, first, second in [('source/build', source, work), ('source/output', source, output_root),
                                 ('build/output distribution', work, output_root)]:
        if first.is_relative_to(second) or second.is_relative_to(first):
            raise ValueError(label + ' paths overlap: ' + str(first) + ' / ' + str(second))
    return source, work, output_root / args.target


def build(args):
    source, work, output = _build_paths(args)
    initial_inputs = _input_snapshot(source)
    # Preserve a virtualenv launcher path; resolving its symlink selects the
    # base interpreter and loses the isolated Nuitka installation.
    compiler = Path(args.compiler_python).absolute()
    probe = "import importlib.metadata,json,platform,sys; print(json.dumps({'python':list(sys.version_info[:3]),'nuitka':importlib.metadata.version('Nuitka'),'base_prefix':sys.base_prefix,'prefix':sys.prefix,'system':platform.system(),'machine':platform.machine(),'libc':platform.libc_ver()}))"
    runtime = json.loads(subprocess.check_output([str(compiler), '-c', probe], text=True))
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}.get(runtime['machine'].lower())
    if (tuple(runtime['python']) != PYTHON_VERSION or runtime['nuitka'] != NUITKA_VERSION
            or runtime['system'] != 'Linux' or arch != TARGETS[args.target][0]):
        raise ValueError('compiler must match pinned CPython/Nuitka and requested native Linux architecture')
    if output.exists() and any(output.iterdir()):
        raise ValueError('output target directory must be absent or empty: ' + str(output))
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    dependency_roots = {}
    for entry in args.dependency_root:
        root, separator, provenance = entry.partition('=')
        if not separator or root in dependency_roots:
            raise ValueError('dependency roots must be distinct ROOT=PROVENANCE entries')
        dependency_roots[root] = provenance
    report = output / 'nuitka-report.xml'
    command = [str(compiler), '-m', 'nuitka', '--mode=standalone', '--include-module=native_config',
               '--include-module=hook_runtime', '--jobs=1', '--output-dir=' + str(work),
               '--output-filename=connect', '--report=' + str(report), '--assume-yes-for-downloads', str(source / 'connect')]
    environment = {**os.environ, 'PATH': str(compiler.parent) + os.pathsep + os.environ.get('PATH', '')}
    with (output / 'build.log').open('w') as log:
        subprocess.run(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    _check_input_snapshot(source, initial_inputs)
    dependencies = dependency_destinations(report, source, dependency_roots,
        {'sys.real_prefix': runtime['base_prefix'], 'sys.prefix': runtime['prefix'], 'cwd': str(source)})
    distribution = work / 'connect.dist'
    actual = {path.relative_to(distribution).as_posix() for path in distribution.rglob('*') if path.is_file()}
    if actual != {'connect', *dependencies}:
        raise ValueError('standalone files differ from compiler provenance: ' + repr(sorted(actual.symmetric_difference({'connect', *dependencies}))))
    verify_elf_target(distribution / 'connect', args.target)
    manifest = make_manifest(distribution, self_owned_roots=['.'], dependency_roots=dependencies)
    audit = audit_artifact(distribution, manifest)
    if not audit['ok']:
        raise ValueError('standalone artifact contains forbidden owned Python: ' + json.dumps(audit))
    for arguments in (['--list-agents'], ['--hook-runtime', '--help']):
        subprocess.run([str(distribution / 'connect'), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
    bundle = output / 'connect'
    shutil.copytree(distribution, bundle)
    archive = output / 'connect.tar.gz'
    pending_archive = output / '.connect.tar.gz.pending'
    package_archive(bundle, pending_archive)
    unpacked = sum(record['size'] for record in manifest['files'])
    if pending_archive.stat().st_size > 64 * 1024 * 1024 or unpacked > 256 * 1024 * 1024 or len(manifest['files']) > 4096:
        raise ValueError('standalone artifact exceeds native installer extraction limits')
    glibc_symbols = set()
    for path in bundle.rglob('*'):
        if path.is_file():
            inspected = subprocess.check_output(['readelf', '--version-info', str(path)], text=True, stderr=subprocess.DEVNULL)
            glibc_symbols.update(re.findall(r'GLIBC_(\d+(?:\.\d+)+)', inspected))
    info = {'schema_version': 1, 'format': 'native-tar-v1', 'target': args.target, 'entrypoint': 'connect/connect',
            'sha256': hashlib.sha256(pending_archive.read_bytes()).hexdigest(), 'compressed_bytes': pending_archive.stat().st_size,
            'unpacked_bytes': unpacked, 'files': len(manifest['files']), 'compiler_runtime': runtime,
            'gcc': subprocess.check_output(['gcc', '--version'], text=True).splitlines()[0],
            'max_glibc_symbol': max(glibc_symbols, key=lambda value: tuple(map(int, value.split('.'))), default=None),
            'source_sha256': {name: initial_inputs[name] for name in OWNED_MODULES},
            'builder_sha256': {'build_native_onboarding.py': initial_inputs['builder'], 'native_artifacts.py': initial_inputs['source_audit']},
            'inputs_stable': True,
            'compiler_report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'command': command}
    try:
        _check_input_snapshot(source, initial_inputs)
    except ValueError:
        pending_archive.unlink(missing_ok=True)
        raise
    os.replace(pending_archive, archive)
    for name, data in [('manifest.json', manifest), ('source-audit.json', audit), ('build-info.json', info)]:
        (output / name).write_text(json.dumps(data, indent=2) + '\n')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--compiler-python', default=sys.executable)
    parser.add_argument('--target', choices=sorted(TARGETS), required=True)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2] / 'ai-vision/agent-plugins/scripts')
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--dependency-root', action='append', required=True, metavar='ROOT=PROVENANCE')
    args = parser.parse_args()
    try:
        info = build(args)
    except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(info, indent=2))


if __name__ == '__main__':
    main()
