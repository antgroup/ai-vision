"""Installation boundaries use synthetic environments and never download packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_runtimes as installer


def clean_source_command(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, stdout=(
        'f97608f178d1ffeca59860195ab7da295f7c8e5f\n' if 'rev-parse' in argv else ''))


PRIVATE_ENV = {
    "NVIDIA_API_KEY": "synthetic-provider-secret",
    "OPENAI_API_KEY": "synthetic-provider-secret",
    "anthropic_auth_token": "synthetic-provider-secret",
    "AWS_SECRET_ACCESS_KEY": "synthetic-provider-secret",
    "LANGFUSE_SECRET_KEY": "synthetic-observation-secret",
    "LANGSMITH_API_KEY": "synthetic-observation-secret",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=synthetic-observation-secret",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://invalid-observation.example",
    "OBSERV_OPENCODE_OTEL_HEADERS": "synthetic-observation-secret",
    "AIVISION_SPACE_KEY": "synthetic-observation-secret",
    "ANT_USERNAME": "synthetic-observation-identity",
    "PI_CODING_AGENT_DIR": "/synthetic/user-config",
    "OPENCODE_CONFIG_DIR": "/synthetic/user-config",
    "HERMES_HOME": "/synthetic/user-config",
    "KIMI_CODE_HOME": "/synthetic/user-config",
}


class InstallerTests(unittest.TestCase):
    def test_pi_installs_host_compatible_codemode_before_resolving_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / 'runtimes'

            def execute(argv, **kwargs):
                self.assertIn('--save-exact', argv)
                destination = Path(argv[argv.index('--prefix') + 1])
                manifest = json.loads((destination / 'package.json').read_text())
                self.assertEqual(manifest['dependencies'], {'@earendil-works/pi-coding-agent': '1.0.2'})
                self.assertEqual(manifest['overrides'], {
                    '@earendil-works/pi-coding-agent': {'@earendil-works/pi-codemode': '1.0.2'},
                })
                return subprocess.CompletedProcess(argv, 0, stdout='')

            with patch.object(installer.shutil, 'which', side_effect=lambda name: 'synthetic-' + name), \
                    patch.object(installer.subprocess, 'run', side_effect=execute):
                path = installer.install(root / 'repo', out, ['pi'])
            self.assertEqual(json.loads(path.read_text())['pi'], [
                'synthetic-node', str(out.resolve() / 'pi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js'),
            ])

    def test_kimi_installs_exact_official_host_and_production_hook_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); out=root/'runtimes'; repo=root/'repo'
            def execute(argv, **kwargs):
                if '@moonshot-ai/kimi-code@2.1.1' in argv:
                    host=out/'kimi/node_modules/@moonshot-ai/kimi-code'
                    (host/'dist').mkdir(parents=True)
                    (host/'package.json').write_text(json.dumps({'version':'2.1.1','bin':{'kimi':'dist/main.mjs'}}))
                    (host/'dist/main.mjs').write_text('/* official executable fixture */')
                if 'cargo' in str(argv[0]):
                    target=Path(argv[argv.index('--target-dir')+1])/'release'
                    target.mkdir(parents=True)
                    (target/('kimihooks.exe' if os.name=='nt' else 'kimihooks')).write_bytes(b'production-hook-fixture')
                return subprocess.CompletedProcess(argv,0,stdout='')
            with patch.object(installer.shutil,'which',side_effect=lambda name:'synthetic-'+name), \
                 patch.object(installer.subprocess,'run',side_effect=execute) as run:
                manifest=installer.install(repo,out,['kimi'])
            runtime=json.loads(manifest.read_text())['kimi']
            self.assertEqual(runtime,['synthetic-node',str(out.resolve()/'kimi/node_modules/@moonshot-ai/kimi-code/dist/main.mjs')])
            hook=Path(runtime[-1]).parent/('kimihooks.exe' if os.name=='nt' else 'kimihooks')
            self.assertEqual(hook.read_bytes(),b'production-hook-fixture')
            commands=[call.args[0] for call in run.call_args_list]
            self.assertTrue(any('@moonshot-ai/kimi-code@2.1.1' in argv for argv in commands))
            cargo=next(argv for argv in commands if 'cargo' in str(argv[0]))
            self.assertIn('--bin',cargo); self.assertIn('kimihooks',cargo)
            self.assertIn(str(repo.resolve()/'ai-vision/agent-plugins/kimi/kimihooks/Cargo.toml'),cargo)
            self.assertFalse(any('replay' in part for argv in commands for part in argv))

    def test_hermes_uses_immutable_official_source_for_unpublished_pypi_release(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(installer.subprocess, 'run', side_effect=clean_source_command) as run:
            installer.install(Path(temporary) / 'repo', Path(temporary) / 'runtimes', ['hermes'])
        install = [call.args[0] for call in run.call_args_list if 'pip' in call.args[0] and 'install' in call.args[0]]
        self.assertIn('-e', install[0], 'official Hermes rejects non-editable wheel builds')
        self.assertEqual(install[0][-1], str(Path(temporary).resolve() / 'runtimes/hermes/source') + '[acp]')
        clone = next(call.args[0] for call in run.call_args_list if 'clone' in call.args[0])
        self.assertEqual(clone[1:-1], ['clone', '--depth', '1', '--branch', 'v2026.9.24',
                                     'https://github.com/NousResearch/hermes-agent.git'])

    def test_hermes_rejects_moved_release_tag_before_installing_source(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout='unexpected-commit\n')) as run:
            with self.assertRaisesRegex(ValueError, 'pinned official release commit'):
                installer.install(Path(temporary) / 'repo', Path(temporary) / 'runtimes', ['hermes'])
        self.assertFalse(any('pip' in call.args[0] for call in run.call_args_list))

    def test_hermes_rejects_edited_source_even_at_the_pinned_commit(self):
        def execute(argv, **kwargs):
            if 'status' in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=' M agent/conversation_loop.py\n')
            return clean_source_command(argv, **kwargs)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(installer.subprocess, 'run', side_effect=execute) as run:
            with self.assertRaisesRegex(ValueError, 'local changes'):
                installer.install(Path(temporary) / 'repo', Path(temporary) / 'runtimes', ['hermes'])
        self.assertFalse(any('pip' in call.args[0] for call in run.call_args_list))

    def test_dsh_install_pins_transitive_official_packages_and_checks_actual_tree(self):
        """A caret transitively upgrading the host must never change its contract."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            repo.mkdir()
            out = root / 'runtimes'
            metadata = {
                '@deepseek-ai/dsh@0.1.7-rc.2': {'dependencies': {
                    '@deepseek-ai/dsh-base': '^0.1.7-rc.2', '@deepseek-ai/cordis': '^4.0.1'}},
                '@deepseek-ai/dsh-base@0.1.7-rc.2': {'dependencies': {
                    '@deepseek-ai/dsh-session': '^0.1.7-rc.2'}},
                '@deepseek-ai/dsh-session@0.1.7-rc.2': {'peerDependencies': {'@deepseek-ai/cordis': '^4.0.1'}},
                '@deepseek-ai/cordis@4.0.1': {},
            }

            def execute(argv, **kwargs):
                if argv[1] == 'view':
                    return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(metadata[argv[2]]))
                self.assertIn('--prefix', argv, 'DSH must install an official npm host, not a Python venv')
                destination = Path(argv[argv.index('--prefix') + 1])
                manifest = json.loads((destination / 'package.json').read_text())
                self.assertEqual(manifest['dependencies'], {'@deepseek-ai/dsh': '0.1.7-rc.2'})
                self.assertEqual(manifest['overrides'], {
                    '@deepseek-ai/dsh': '0.1.7-rc.2', '@deepseek-ai/dsh-base': '0.1.7-rc.2',
                    '@deepseek-ai/dsh-session': '0.1.7-rc.2', '@deepseek-ai/cordis': '4.0.1'})
                packages = {'': {'dependencies': manifest['dependencies']}}
                for name, version in manifest['overrides'].items():
                    package = destination / 'node_modules' / name
                    package.mkdir(parents=True)
                    (package / 'package.json').write_text(json.dumps({'name': name, 'version': version,
                        **({'bin': {'dsh': 'bin/dsh.js'}} if name.endswith('/dsh') else {})}))
                    packages['node_modules/' + name] = {'version': version}
                (destination / 'package-lock.json').write_text(json.dumps({'packages': packages}))
                return subprocess.CompletedProcess(argv, 0)

            with patch.object(installer.shutil, 'which', side_effect=lambda name: 'synthetic-' + name), \
                    patch.object(installer.subprocess, 'run', side_effect=execute), \
                    patch.dict(installer.VERSIONS, {'dsh01': '0.1.7-rc.2'}):
                path = installer.install(repo, out, ['dsh01'])
            runtime = json.loads(path.read_text())['dsh01']
            self.assertEqual(runtime, ['synthetic-node', str(out.resolve() / 'dsh01/node_modules/@deepseek-ai/dsh/bin/dsh.js')])
            self.assertEqual(json.loads((out / 'dsh01/dsh-pins.json').read_text())['@deepseek-ai/dsh-session'], '0.1.7-rc.2')

    def test_windows_dsh_install_uses_private_appdata_for_npm(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            out = root / 'runtimes'
            destination = out / 'dsh02'
            home = destination / 'install-home'
            upstream = '@deepseek-ai/dsh'
            version = installer.VERSIONS['dsh02']
            windows = SimpleNamespace(name='nt', environ={
                'Path': 'synthetic-system-path',
                'Home': 'host-home',
                'UserProfile': 'host-profile',
                'AppData': 'host-roaming',
                'localAppData': 'host-local',
                'Node_Auth_Token': 'host-registry-token',
                'Npm_Config_UserConfig': 'host-npm-config',
            })

            def execute(argv, *, env, **kwargs):
                environment = {key.upper(): value for key, value in env.items()}
                self.assertEqual(environment['HOME'], str(home))
                self.assertEqual(environment['USERPROFILE'], str(home))
                self.assertEqual(environment.get('APPDATA'), str(home / 'AppData/Roaming'))
                self.assertEqual(environment.get('LOCALAPPDATA'), str(home / 'AppData/Local'))
                self.assertTrue(Path(environment['APPDATA']).is_dir())
                self.assertTrue(Path(environment['LOCALAPPDATA']).is_dir())
                self.assertNotIn('NODE_AUTH_TOKEN', environment)
                self.assertEqual(Path(environment['NPM_CONFIG_USERCONFIG']), home / '.npmrc')
                self.assertEqual((home / '.npmrc').read_text(encoding='utf-8'), '')
                if argv[1] == 'view':
                    return subprocess.CompletedProcess(argv, 0, stdout='{}')
                self.assertEqual(argv[1], 'install')
                package = destination / 'node_modules' / upstream
                package.mkdir(parents=True)
                (package / 'package.json').write_text(json.dumps({'version': version, 'bin': {'dsh': 'cli.js'}}))
                lock = {'packages': {'node_modules/' + upstream: {'version': version}}}
                (destination / 'package-lock.json').write_text(json.dumps(lock))
                return subprocess.CompletedProcess(argv, 0)

            with (
                patch.object(installer, 'os', windows),
                patch.object(installer.shutil, 'which', side_effect=lambda name: 'synthetic-' + name),
                patch.object(installer.subprocess, 'run', side_effect=execute) as run,
            ):
                installer.install(root / 'repo', out, ['dsh02'])
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ['view', 'install'])

    def test_dsh_tree_validation_rejects_nested_newer_runtime_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            pins = {'@deepseek-ai/dsh': '0.1.7-rc.2', '@deepseek-ai/dsh-session': '0.1.7-rc.2'}
            packages = {'node_modules/@deepseek-ai/dsh': {'version': '0.1.7-rc.2'},
                'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-session': {'version': '0.2.0-rc.2'}}
            (destination / 'package-lock.json').write_text(json.dumps({'packages': packages}))
            host = destination / 'node_modules/@deepseek-ai/dsh'
            host.mkdir(parents=True)
            (host / 'package.json').write_text(json.dumps({'version': '0.1.7-rc.2'}))
            with self.assertRaisesRegex(ValueError, 'dsh-session.*0.2.0'):
                installer.validate_dsh_tree(destination, pins)

    def test_dsh_tree_validation_keeps_other_platform_optional_pins_without_requiring_their_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            name = '@deepseek-ai/libreoffice-kit-win32-x64'
            pins = {name: '0.1.0'}
            (destination / 'package-lock.json').write_text(json.dumps({'packages': {
                'node_modules/' + name: {'version': '0.1.0', 'optional': True, 'os': ['win32'], 'cpu': ['x64']}}}))
            installer.validate_dsh_tree(destination, pins)

    def test_default_command_environment_is_filtered_and_empty_override_stays_empty(self):
        inherited = {**PRIVATE_ENV, "PATH": "synthetic-path", "HTTPS_PROXY": "http://127.0.0.1:13659"}
        with patch.dict(os.environ, inherited, clear=True), \
                patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            installer.command(["synthetic-cli"])
            env = run.call_args.kwargs["env"]
            self.assertEqual(env, {"PATH": "synthetic-path", "HTTPS_PROXY": "http://127.0.0.1:13659"})
            installer.command(["synthetic-cli"], env={})
            self.assertEqual(run.call_args.kwargs["env"], {})

    def test_disallowed_credential_values_are_not_read(self):
        class GuardedEnvironment(dict):
            def __getitem__(self, key):
                if key in PRIVATE_ENV:
                    raise AssertionError("installer attempted to read a credential value")
                return super().__getitem__(key)
        source = GuardedEnvironment({**PRIVATE_ENV, "Path": "synthetic-path", "PIP_CONFIG_FILE": "synthetic-config"})
        self.assertEqual(installer.installer_environment(source),
                         {"Path": "synthetic-path", "PIP_CONFIG_FILE": "synthetic-config"})

    def test_direct_command_child_cannot_inherit_provider_or_telemetry_settings(self):
        # Execute a real child, preserving actual OS startup necessities; all
        # provider/observation data in this test is explicitly synthetic.
        inherited = {key: os.environ[key] for key in os.environ
                     if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
        inherited.update(PRIVATE_ENV)
        inherited.update(HTTPS_PROXY="http://127.0.0.1:13659", PIP_INDEX_URL="https://pypi.org/simple",
                         npm_config_cache="synthetic-cache", PYTHONUTF8="1")
        script = ("import os; "
                  "assert not any(key in os.environ for key in " + repr(list(PRIVATE_ENV)) + "); "
                  "assert os.environ['HTTPS_PROXY'] == 'http://127.0.0.1:13659'; "
                  "assert os.environ['PIP_INDEX_URL'] == 'https://pypi.org/simple'; "
                  "assert os.environ['npm_config_cache'] == 'synthetic-cache'")
        installer.command([sys.executable, "-c", script], env=inherited)

    def test_all_runtime_install_commands_share_clean_environment_and_private_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            out = root / "private-runtimes"
            inherited = {**PRIVATE_ENV, "Path": "synthetic-system-path", "SystemRoot": "synthetic-system-root",
                         "ComSpec": "synthetic-command-processor", "PATHEXT": ".EXE;.CMD", "TEMP": str(root),
                         "HOME": str(root / "user-home"), "HTTPS_PROXY": "http://127.0.0.1:13659",
                         "NO_PROXY": "127.0.0.1,localhost", "PIP_CONFIG_FILE": "synthetic-pip-config",
                         "UV_INDEX_URL": "https://pypi.org/simple", "NPM_CONFIG_USERCONFIG": "synthetic-npm-config",
                         "RUSTUP_HOME": str(root / "rustup"), "RUSTUP_TOOLCHAIN": "stable",
                         "NODE_AUTH_TOKEN": "synthetic-registry-token", "npm_config_cache": "old-user-cache",
                         "NPM_CONFIG_CACHE": "old-uppercase-cache"}
            with patch.dict(os.environ, inherited, clear=True), \
                    patch.object(installer.shutil, "which", side_effect=lambda name: "synthetic-" + name), \
                    patch.object(installer.subprocess, "run", side_effect=clean_source_command) as run:
                path = installer.install(repo, out, ["pi", "opencode", "hermes"])
            self.assertEqual(len(run.call_args_list), 10, "all npm, Git and venv/pip commands must be checked")
            seed_calls = [call for call in run.call_args_list
                          if "@opencode-ai/plugin@1.18.34" in call.args[0]]
            self.assertEqual(len(seed_calls), 1, "install a real official configuration dependency seed")
            seed_argv = seed_calls[0].args[0]
            self.assertEqual(Path(seed_argv[seed_argv.index("--prefix") + 1]),
                             out.resolve() / "opencode/opencode-config-seed")
            for flag in ("--save-exact", "--package-lock=true", "--ignore-scripts", "--workspaces=false"):
                self.assertIn(flag, seed_argv)
            for call in run.call_args_list:
                env = call.kwargs["env"]
                self.assertIsInstance(env, dict, "no installer command may implicitly inherit the environment")
                normalized = {key.upper(): value for key, value in env.items()}
                for key in PRIVATE_ENV:
                    self.assertNotIn(key.upper(), normalized)
                for key, value in inherited.items():
                    if key not in PRIVATE_ENV and key.upper() != "NPM_CONFIG_CACHE":
                        self.assertEqual(normalized.get(key.upper()), value, key)
                self.assertEqual(env["npm_config_cache"], str(out.resolve() / "npm-cache"))
                self.assertEqual(sum(key.upper() == "NPM_CONFIG_CACHE" for key in env), 1)
                self.assertTrue(call.kwargs["check"])
                self.assertEqual(call.kwargs["timeout"], 900)
            runtimes = json.loads(path.read_text())
            self.assertEqual(set(runtimes), {"pi", "opencode", "hermes"})
            self.assertNotIn("synthetic-registry-token", path.read_text())
            self.assertNotIn("synthetic-provider-secret", path.read_text())
