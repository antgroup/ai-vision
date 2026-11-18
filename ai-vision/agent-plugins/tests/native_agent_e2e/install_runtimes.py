"""Install official pinned native runtimes into a private CI/local directory."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys

from run_suite import APPS, VERSIONS, selected


def installer_environment(source=None):
    """Inherit OS/package-install settings, never Agent/provider telemetry state.

    Names are compared case-insensitively for Windows, but preserved verbatim.
    Filter names before looking up values so unrelated credentials are not read.
    Registry authentication and explicitly configured install proxies belong to
    package installation; they are not serialized into runtime/artifact files.
    """
    source = os.environ if source is None else source
    names = {
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
        "TMP", "TEMP", "TMPDIR", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "NODE_AUTH_TOKEN", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS", "VIRTUAL_ENV",
        "PYTHONUTF8", "PYTHONNOUSERSITE", "PYTHONIOENCODING", "PYTHONUNBUFFERED",
        "CI", "GITHUB_ACTIONS", "RUNNER_OS", "RUNNER_TEMP", "LANG", "TZ", "TERM", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
        "COLORTERM", "NO_COLOR", "FORCE_COLOR", "XDG_CACHE_HOME", "NUMBER_OF_PROCESSORS",
        "CC", "CXX", "CFLAGS", "CPPFLAGS", "LDFLAGS", "ARCHFLAGS", "MAKEFLAGS",
        "SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "RUSTFLAGS", "INCLUDE", "LIB", "LIBPATH",
    }
    prefixes = ("NPM_", "PIP_", "UV_", "LC_", "CMAKE_", "CARGO_")
    return {key: source[key] for key in source
            if key.upper() in names or key.upper().startswith(prefixes)}


def command(argv, *, env=None, capture_output=False):
    return subprocess.run([str(part) for part in argv], env=installer_environment(env), check=True,
                          timeout=900, capture_output=capture_output, text=True)


def dsh_dependency_pins(npm, version, env):
    """Resolve every official package at its declared lower bound, never latest.

    Early DSH releases published caret ranges even between prerelease host
    packages. Override their complete graph to the selected host release;
    Cordis/Schemastery keep the explicitly declared compatible base version.
    """
    pins = {'@deepseek-ai/dsh': version}
    visited = set()

    def metadata(item):
        name, exact = item
        result = subprocess.run([npm, 'view', name + '@' + exact, '--json',
            '--registry=https://registry.npmjs.org'], env=installer_environment(env),
            check=True, capture_output=True, text=True, timeout=900)
        return name, exact, json.loads(result.stdout)

    while pending := sorted((name, exact) for name, exact in pins.items() if (name, exact) not in visited):
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, exact, manifest in pool.map(metadata, pending):
                visited.add((name, exact))
                for group in ('dependencies', 'optionalDependencies', 'peerDependencies'):
                    for child, declared in manifest.get(group, {}).items():
                        if not child.startswith('@deepseek-ai/'):
                            continue
                        wanted = version if child == '@deepseek-ai/dsh' or child.startswith('@deepseek-ai/dsh-') else declared.lstrip('^~')
                        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[\w.]+)?', wanted):
                            raise ValueError(f'Cannot exactly pin {child}: {declared}')
                        old = pins.get(child)
                        if old is None or tuple(map(int, wanted.split('-')[0].split('.'))) > tuple(map(int, old.split('-')[0].split('.'))):
                            pins[child] = wanted
    return dict(sorted(pins.items()))


def validate_dsh_tree(destination, pins):
    """Check both npm's resolved lock and each actual package it installed."""
    packages = json.loads((destination / 'package-lock.json').read_text())['packages']
    found = set()
    for relative, record in packages.items():
        match = re.search(r'(?:^|/)node_modules/(@deepseek-ai/[^/]+)$', relative)
        if not match:
            continue
        name = match.group(1)
        actual = record.get('version')
        if name not in pins or actual != pins[name]:
            raise ValueError(f'Unpinned DSH package {name}: {actual}')
        manifest_path = destination / relative / 'package.json'
        # npm locks all OS-specific optional binaries but only materializes
        # the current platform. Their lock entries must still remain pinned.
        if not manifest_path.exists() and record.get('optional') and (record.get('os') or record.get('cpu')):
            found.add(name)
            continue
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('version') != pins[name]:
            raise ValueError(f'Actual DSH package {name} differs from the fixed lock')
        found.add(name)
    if missing := set(pins) - found:
        raise ValueError('Incomplete DSH dependency tree: ' + ', '.join(sorted(missing)))


def install(repo, out, apps):
    repo, out = repo.resolve(), out.resolve()
    if out.is_relative_to(repo):
        raise ValueError("Install native runtimes outside the Git checkout")
    out.mkdir(parents=True, exist_ok=True)
    node, npm = shutil.which("node"), shutil.which("npm")
    if any(app in {'pi', 'opencode', 'kimi'} or app.startswith('dsh') for app in apps) and (not node or not npm):
        raise ValueError("Native Pi/OpenCode/DSH/Kimi acceptance requires Node.js and npm")
    env = {key: value for key, value in installer_environment().items() if key.upper() != "NPM_CONFIG_CACHE"}
    env["npm_config_cache"] = str(out / "npm-cache")
    runtimes = {}
    for app in apps:
        destination = out / app
        if app.startswith('dsh'):
            destination.mkdir(parents=True, exist_ok=True)
            private_home = destination / 'install-home'
            private_home.mkdir(exist_ok=True)
            npm_config = private_home / '.npmrc'
            npm_config.write_text('', encoding='utf-8')
            dsh_env = {key: value for key, value in env.items() if key.upper() not in {
                'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'NODE_AUTH_TOKEN', 'NPM_CONFIG_USERCONFIG'}}
            dsh_env.update(HOME=str(private_home), USERPROFILE=str(private_home),
                npm_config_userconfig=str(npm_config), XDG_CACHE_HOME=str(private_home / '.cache'))
            pins = dsh_dependency_pins(npm, VERSIONS[app], dsh_env)
            (destination / 'package.json').write_text(json.dumps({'private': True,
                'dependencies': {'@deepseek-ai/dsh': VERSIONS[app]}, 'overrides': pins}, indent=2) + '\n')
            command([npm, 'install', '--prefix', destination, '--save-exact', '--package-lock=true',
                '--workspaces=false', '--registry=https://registry.npmjs.org'], env=dsh_env)
            validate_dsh_tree(destination, pins)
            (destination / 'dsh-pins.json').write_text(json.dumps(pins, indent=2) + '\n')
            host = destination / 'node_modules/@deepseek-ai/dsh'
            cli = json.loads((host / 'package.json').read_text())['bin']['dsh']
            runtimes[app] = [node, str(host / cli)]
        elif app == 'kimi':
            cargo=shutil.which('cargo')
            if not cargo:
                raise ValueError('Kimi native acceptance requires Cargo to build production kimihooks')
            command([npm,'install','--prefix',destination,'--save-exact','--package-lock=true',
                '--workspaces=false','--registry=https://registry.npmjs.org','@moonshot-ai/kimi-code@'+VERSIONS[app]],env=env)
            host=destination/'node_modules/@moonshot-ai/kimi-code'
            manifest=json.loads((host/'package.json').read_text())
            if manifest.get('version')!=VERSIONS[app] or not isinstance(manifest.get('bin',{}).get('kimi'),str):
                raise ValueError('Installed Kimi package differs from the pinned official CLI')
            cli=(host/manifest['bin']['kimi']).resolve()
            if not cli.is_relative_to(host.resolve()) or not cli.is_file():
                raise ValueError('Pinned official Kimi CLI entry point is missing')
            target=destination/'hook-target'
            command([cargo,'build','--release','--manifest-path',repo/'ai-vision/agent-plugins/kimi/kimihooks/Cargo.toml',
                '--target-dir',target,'--bin','kimihooks'],env=env)
            binary='kimihooks.exe' if os.name=='nt' else 'kimihooks'
            shutil.copy2(target/'release'/binary,cli.parent/binary)
            runtimes[app]=[node,str(cli)]
        elif app in ("pi", "opencode"):
            package = "@earendil-works/pi-coding-agent" if app == "pi" else "opencode-ai"
            if app == "pi":
                # Pi 1.0.2 imports a declaration export removed by later
                # pi-codemode releases admitted by its upstream caret range.
                destination.mkdir(parents=True, exist_ok=True)
                (destination / 'package.json').write_text(json.dumps({
                    'private': True,
                    'dependencies': {package: VERSIONS[app]},
                    'overrides': {package: {'@earendil-works/pi-codemode': '1.0.2'}},
                }, indent=2) + '\n', encoding='utf-8')
            command([npm, "install", "--prefix", destination, "--save-exact", "--package-lock=false", "--workspaces=false",
                     "--registry=https://registry.npmjs.org", package + "@" + VERSIONS[app]], env=env)
            if app == "opencode":
                # The official host waits for these config dependencies before
                # loading external plugins. Install their real pinned package,
                # lock and dependency tree once; drivers copy this private seed
                # into each fresh config directory without changing host code.
                command([npm, "install", "--prefix", destination / "opencode-config-seed",
                         "--save-exact", "--package-lock=true", "--ignore-scripts", "--workspaces=false",
                         "--registry=https://registry.npmjs.org", "@opencode-ai/plugin@" + VERSIONS[app]], env=env)
            cli = (destination / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js" if app == "pi" else
                   destination / "node_modules/opencode-ai/bin/opencode.exe")
            # Pi publishes a Node CLI; OpenCode's npm postinstall stages an
            # official native executable at bin/opencode.exe on every OS.
            runtimes[app] = [node, str(cli)] if app == "pi" else [str(cli)]
        else:
            command([sys.executable, "-m", "venv", destination], env=env)
            python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            cli = destination / ("Scripts/hermes.exe" if os.name == "nt" else "bin/hermes")
            # Current Hermes deliberately rejects wheel/sdist builds and is
            # distributed from source. Keep its editable checkout private and
            # pin the release commit rather than a branch or older PyPI latest.
            hermes_source = {"0.21.5": ("v2026.9.24", "f97608f178d1ffeca59860195ab7da295f7c8e5f")}
            if VERSIONS[app] in hermes_source:
                tag, commit = hermes_source[VERSIONS[app]]
                source = destination / "source"
                git = shutil.which("git")
                if not git:
                    raise ValueError("Current Hermes native runtime installation requires Git")
                if not source.exists():
                    command([git, "clone", "--depth", "1", "--branch", tag,
                             "https://github.com/NousResearch/hermes-agent.git", source], env=env)
                actual = command([git, "-C", source, "rev-parse", "HEAD"], env=env, capture_output=True).stdout.strip()
                if actual != commit:
                    raise ValueError("Hermes source checkout differs from the pinned official release commit")
                dirty = command([git, "-C", source, "status", "--porcelain", "--untracked-files=all"],
                                env=env, capture_output=True).stdout.strip()
                if dirty:
                    raise ValueError("Hermes source checkout has local changes; use a fresh private runtime directory")
                # Local editable syntax works across pip versions; VCS URL
                # extras changed syntax between older pip and pip 26.
                requirement = ["-e", str(source) + "[acp]"]
            else:
                requirement = ["hermes-agent[acp]==" + VERSIONS[app]]
            command([python, "-m", "pip", "install", *requirement], env=env)
            command([python, "-m", "pip", "install", "-r", Path(__file__).with_name("requirements-hermes.txt")], env=env)
            command([python, "-m", "pip", "install", "--no-deps", "-e", repo / "ai-vision/agent-plugins/hermes/hermes-core"], env=env)
            runtimes[app] = [str(cli)]
    path = out / "runtimes.json"
    path.write_text(json.dumps(runtimes, indent=2) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apps", default=",".join(APPS))
    args = parser.parse_args()
    print(install(args.repo, args.out, selected(args.apps, APPS)))


if __name__ == "__main__":
    main()
