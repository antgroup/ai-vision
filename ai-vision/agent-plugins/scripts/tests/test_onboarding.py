import contextlib
import importlib.machinery
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import native_config as config
import hook_runtime as runtime
loader = importlib.machinery.SourceFileLoader('connect', str(SCRIPTS / 'connect'))
spec = importlib.util.spec_from_loader('connect', loader)
installer = importlib.util.module_from_spec(spec)
loader.exec_module(installer)


class ConfigTests(unittest.TestCase):
    def test_hooks_are_idempotent_and_preserve_other_plugins(self):
        unrelated = {'matcher': '*', 'hooks':[{'type':'command','command':'other-plugin'}]}
        doc = {'env':{'MY_CONFIG':'keep'},'hooks':{'Stop':[unrelated]}}
        command = shlex.join(['/path with space/python', '/somewhere/hook_runtime.py', '--binary', '/path/cdxhooks'])
        for _ in range(2):
            config.merge_hooks(doc, command, 'cdxhooks', ['Stop','SessionEnd'])
        self.assertEqual(len(doc['hooks']['Stop']), 2)
        self.assertEqual(doc['hooks']['Stop'][0], unrelated)
        self.assertEqual(doc['env'], {'MY_CONFIG':'keep'})
        self.assertEqual(doc['hooks']['SessionEnd'][0]['hooks'][0]['timeout'], 3)

    def test_false_toml_feature_preserves_other_tables(self):
        source = 'model="example"\n[features] # comment\nhooks=false # preserve\nother=true\n[tools]\nhooks=false\n'
        result = config.enable_feature(source, 'hooks')
        self.assertEqual(tomllib.loads(result), {'model':'example','features':{'hooks':True,'other':True},'tools':{'hooks':False}})
        self.assertIn('# preserve', result)
        self.assertEqual(config.enable_feature(result, 'hooks'), result)

    def test_kimi_block_is_repeatable_with_spaced_path(self):
        source = 'model="kimi"\n[[hooks]]\nevent="Stop"\ncommand="other-hook"\n'
        result = config.kimi_hooks(source, "'/a path/hook.py'")
        self.assertEqual(config.kimi_hooks(result, "'/a path/hook.py'"), result)
        hooks = tomllib.loads(result)['hooks']
        self.assertEqual(len(hooks), 3)
        self.assertEqual(hooks[0]['command'], 'other-hook')

    def test_transaction_restores_original_and_removes_new_file(self):
        with tempfile.TemporaryDirectory() as temp:
            original, new = Path(temp)/'settings.json', Path(temp)/'new.json'
            original.write_bytes(b'{"untouched": true}\n')
            with self.assertRaises(RuntimeError):
                with config.ConfigTransaction() as tx:
                    tx.json(original, {'modified':True})
                    tx.json(new, {'secret':'test-placeholder'}, private=True)
                    raise RuntimeError('native registration failed')
            self.assertEqual(original.read_bytes(), b'{"untouched": true}\n')
            self.assertFalse(new.exists())

    def test_invalid_config_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'settings.json';file.write_text('{invalid')
            with self.assertRaises(ValueError): config.read_object(file)
            self.assertEqual(file.read_text(), '{invalid')

    def test_private_configuration_and_backup_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'ingest.json';file.write_text('old')
            with config.ConfigTransaction() as tx: tx.json(file, {'secret':'placeholder'}, private=True)
            if os.name!='nt':
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(file.with_name(file.name+'.aivision-backup').stat().st_mode & 0o777, 0o600)


class InstallerTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'MiniMax Code CLI PoC uses a POSIX launcher')
    def test_minimax_code_market_grant_installs_launcher_and_reuses_credential(self):
        agent_uuid = '5bdb516f-502f-4f76-914a-d8e7e44ced88'
        base = 'https://vision.test'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "profile with 'quotes'"
            root.mkdir()
            managed = root / 'aivision/minimax-code'
            launcher = managed / 'run'
            model_host = Path(temp) / 'mcode'
            node = Path(temp) / 'node'
            model_host.write_text('#!/bin/sh\nprintf "0.7.2\\n"\n')
            node.write_text('#!/bin/sh\nprintf "%s\\n" "$MINIMAX_DATA_DIR" "$@"\n')
            model_host.chmod(0o700)
            node.chmod(0o700)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as package:
                for name, payload in {'package/package.json': b'{"name":"observ-minimax-code"}',
                                      'package/dist/index.js': b'// installed wrapper fixture'}.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    package.addfile(member, io.BytesIO(payload))
            bodies = {'native_config.py': (SCRIPTS/'native_config.py').read_bytes(),
                      'wrapper.tgz': archive.getvalue()}
            def artifact(name):
                return {'name': name, 'url': base+'/api/'+name, 'sha256': hashlib.sha256(bodies[name]).hexdigest()}
            catalog = {'agents': [{'key': 'minimax-code', 'version': 'test',
                'plugin': {'key': 'minimax-code', 'host': 'mcode', 'name': 'MiniMax Code CLI (PoC)',
                           'package': 'observ-minimax-code', 'product': 'MiniMaxCode'},
                'downloads': [{**artifact('wrapper.tgz'), 'platform': 'all', 'arch': 'all',
                               'target_filename': 'observ-minimax-code.tgz', 'collection_modes': ['full', 'privacy']}]}],
                'support_files': [artifact('native_config.py')]}
            authorization = 'aiv-agent-test-credential'
            requests = []
            confirm_fails = True
            def request(url, body=None, headers=None):
                requests.append(url.rsplit('/', 1)[-1])
                token = {'space_id': 'space', 'agent_uuid': agent_uuid, 'agent_product': 'MiniMaxCode',
                         'endpoint': base+'/v1/traces', 'token': 'test-token', 'expires_in': 900}
                if url.endswith('/catalog.json'):
                    data = catalog
                elif url.endswith('/exchange'):
                    self.assertEqual(headers['Authorization'], 'Bearer aiv-install-test-ticket')
                    self.assertEqual(body, {'space_id': 'space', 'agent_uuid': agent_uuid})
                    data = {**token, 'agent_credential': authorization}
                elif url.endswith('/ingest-tokens'):
                    self.assertEqual(headers['Authorization'], 'Bearer '+authorization)
                    data = token
                elif url.endswith('/installation-complete'):
                    self.assertEqual(headers['Authorization'], 'Bearer '+authorization)
                    self.assertTrue(os.access(launcher, os.X_OK))
                    self.assertEqual((managed/'ingest.json').stat().st_mode & 0o777, 0o600)
                    if confirm_fails:
                        raise installer.ConnectError('Confirmation unavailable')
                    data = {'space_id': 'space', 'agent_uuid': agent_uuid, 'status': 1}
                else:
                    return bodies[url.rsplit('/', 1)[-1]]
                return json.dumps({'code': 0, 'data': data}).encode()
            def which(name):
                return str({'mcode': model_host, 'node': node}[Path(name).name])
            args = SimpleNamespace(agent='minimax-code', agent_uuid=agent_uuid, base_url=base,
                config_dir=str(root), dry_run=False, collection_mode=None, space_key=None,
                space_key_file=None, space_id='space', installation_token='aiv-install-test-ticket')
            with patch.object(installer, 'request_bytes', side_effect=request), patch.object(installer.shutil, 'which', side_effect=which), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(installer.ConnectError, 'Confirmation unavailable'):
                    installer.install(args)
                self.assertFalse(launcher.exists())
                self.assertFalse((managed/'ingest.json').exists())
                confirm_fails = False
                installer.install(args)
                status = installer.installation_status(args)
            self.assertEqual(requests.count('exchange'), 1)
            self.assertEqual(requests.count('installation-complete'), 2)
            self.assertEqual(status['registration'], 'verified')
            self.assertEqual(status['credential'], 'valid')
            self.assertEqual(status['collection_mode'], 'privacy')
            self.assertFalse((managed/'pending-installation.json').exists())
            arguments = subprocess.check_output([str(launcher), 'a prompt', '--continue'], text=True).splitlines()
            self.assertEqual(arguments[0], str(root))
            self.assertTrue(arguments[1].endswith('/dist/index.js'))
            self.assertEqual(arguments[2:], ['--ingest-config', str(managed/'ingest.json'), '--mcode',
                                             str(model_host), '--', 'a prompt', '--continue'])

    def test_invalid_agent_id_is_rejected_before_network_or_credentials(self):
        for value in (None, '', '{agentUuid}', 'invalid'):
            with self.subTest(value=value), patch.object(installer, 'request_data') as request, patch.object(installer.getpass, 'getpass') as prompt:
                with self.assertRaisesRegex(installer.ConnectError, 'generate the installation command'):
                    installer.install(SimpleNamespace(agent_uuid=value))
                request.assert_not_called()
                prompt.assert_not_called()

    def test_finds_and_checks_uv_python_without_downloading(self):
        def run(command, **kwargs):
            if command[0] == 'uv':
                self.assertIn('--no-python-downloads', command)
                return SimpleNamespace(returncode=0, stdout='/managed/python3.12\n')
            if command[0] == '/managed/python3.12':
                return SimpleNamespace(returncode=0, stdout='/managed/python3.12\n')
            return SimpleNamespace(returncode=1, stdout='')
        with patch.object(installer.shutil, 'which', return_value='/bin/uv'), patch.object(installer.subprocess, 'run', side_effect=run), patch.dict(os.environ, {'AIVISION_PYTHON': ''}):
            self.assertEqual(installer.compatible_python(), '/managed/python3.12')

    def test_missing_compatible_python_gives_actionable_error(self):
        with patch.object(installer.shutil, 'which', return_value=None), patch.object(installer.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(installer.ConnectError, 'uv python install 3.12'):
                installer.compatible_python()

    def test_confirmation_follows_native_registration_and_failure_rolls_back(self):
        agent_uuid = '5bdb516f-502f-4f76-914a-d8e7e44ced88'
        base = 'https://vision.test'
        for browser, fail in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(browser=browser, fail=fail), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                original = 'model="preserved"\n'
                (root/'config.toml').write_text(original)
                bodies = {'native_config.py': (SCRIPTS/'native_config.py').read_bytes(),
                          'hook_runtime.py': (SCRIPTS/'hook_runtime.py').read_bytes(),
                          'cdxhooks': b'#!/bin/sh\nprintf "cdxhooks test\\n"\n'}
                def artifact(name):
                    return {'name': name, 'url': base+'/api/'+name, 'sha256': hashlib.sha256(bodies[name]).hexdigest()}
                catalog = {'agents': [{'key': 'codex', 'version': 'test',
                    'plugin': {'key': 'codex', 'host': 'codex', 'name': 'Codex', 'binary': 'cdxhooks', 'product': 'Codex'},
                    'downloads': [{**artifact('cdxhooks'), 'platform': 'all', 'arch': 'all', 'target_filename': 'cdxhooks'}]}],
                    'support_files': [artifact(name) for name in ('native_config.py', 'hook_runtime.py')]}
                confirmed = []
                exchanges = []
                credential = 'aiv-agent-test-credential' if browser else 'test-key'
                def request(url, body=None, headers=None):
                    if url.endswith('catalog.json'):
                        data = catalog
                    elif url.endswith(('/ingest-tokens', '/agent-installations/exchange')):
                        data = {'space_id': 'space', 'agent_uuid': agent_uuid, 'agent_product': 'Codex',
                                'endpoint': base+'/v1/traces', 'token': 'test-token', 'expires_in': 900}
                        if url.endswith('/exchange'):
                            exchanges.append(url)
                            self.assertEqual(headers['Authorization'], 'Bearer aiv-install-test-ticket')
                            self.assertEqual(body, {'space_id': 'space', 'agent_uuid': agent_uuid})
                            data['agent_credential'] = credential
                        else:
                            self.assertEqual(headers['Authorization'], 'Bearer '+credential)
                    elif url.endswith('/installation-complete'):
                        self.assertEqual(headers['Authorization'], 'Bearer '+credential)
                        self.assertTrue((root/'aivision/codex/installation.json').is_file())
                        self.assertEqual(len(json.loads((root/'hooks.json').read_text())['hooks']), 5)
                        confirmed.append(url)
                        if fail:
                            raise installer.ConnectError('Confirmation unavailable')
                        data = {'space_id': 'space', 'agent_uuid': agent_uuid, 'status': 1}
                    else:
                        return bodies[url.rsplit('/', 1)[-1]]
                    return json.dumps({'code': 0, 'data': data}).encode()
                args = SimpleNamespace(agent_uuid=agent_uuid, agent='codex', base_url=base, config_dir=temp,
                    dry_run=False, space_key=None if browser else credential, space_key_file=None, space_id='space',
                    installation_token='aiv-install-test-ticket' if browser else None)
                runtime_probe = (
                    patch.object(installer, 'check_binary_runtime')
                    if os.name == 'nt' else contextlib.nullcontext()
                )
                with (
                    patch.object(installer, 'request_bytes', side_effect=request),
                    patch.object(installer.shutil, 'which', return_value='/bin/codex'),
                    patch.object(installer, 'run', return_value='hooks enabled true'),
                    patch('builtins.print'),
                    patch.object(installer.getpass, 'getpass') as prompt,
                    runtime_probe,
                ):
                    was_failure = fail
                    if not fail:
                        with patch.object(installer, 'check_binary_runtime', side_effect=installer.ConnectError('GLIBC_2.34 unavailable')):
                            with self.assertRaisesRegex(installer.ConnectError, 'GLIBC_2.34'):
                                installer.install(args)
                        self.assertEqual(confirmed, [])
                        self.assertEqual((root/'config.toml').read_text(), original)
                        self.assertFalse((root/'hooks.json').exists())
                        self.assertFalse((root/'aivision/codex/ingest.json').exists())
                        self.assertFalse((root/'aivision/codex/installation.json').exists())
                        self.assertEqual(list((root/'aivision/codex/versions').iterdir()), [])
                    if fail:
                        with self.assertRaisesRegex(installer.ConnectError, 'Confirmation unavailable'):
                            installer.install(args)
                        self.assertEqual((root/'config.toml').read_text(), original)
                        self.assertFalse((root/'hooks.json').exists())
                        self.assertFalse((root/'aivision/codex/ingest.json').exists())
                        self.assertEqual(list((root/'aivision/codex/versions').iterdir()), [])
                        if browser:
                            recovery = root/'aivision/codex/pending-installation.json'
                            self.assertEqual(json.loads(recovery.read_text())['agent_credential'], credential)
                            if os.name != 'nt':
                                self.assertEqual(recovery.stat().st_mode & 0o777, 0o600)
                        fail = False
                    installer.install(args)
                    installer.install(args)
                    hooks = json.loads((root/'hooks.json').read_text())['hooks']
                    self.assertTrue(all(len(entries) == 1 for entries in hooks.values()))
                    self.assertEqual(tomllib.loads((root/'config.toml').read_text())['model'], 'preserved')
                    prompt.assert_not_called()
                self.assertEqual(len(confirmed), 3 if was_failure else 2)
                self.assertEqual(len(exchanges), 1 if browser else 0)
                self.assertFalse((root/'aivision/codex/pending-installation.json').exists())
                if browser:
                    saved = json.loads((root/'aivision/codex/ingest.json').read_text())
                    self.assertEqual(saved['agent_credential'], credential)
                    self.assertNotIn('space_key', saved)
                    self.assertNotIn(args.installation_token, json.dumps(saved))

    def test_rejected_browser_grant_does_not_fall_back_to_space_key_prompt(self):
        args = SimpleNamespace(installation_token='aiv-install-expired', agent_uuid='agent', space_id='space')
        with tempfile.TemporaryDirectory() as temp, patch.object(installer, 'request_data', side_effect=installer.ConnectError('expired')), patch.object(installer.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(installer.ConnectError, 'expired'):
                installer.install_authorization(args, 'https://vision.test', Path(temp))
            prompt.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_hook_renews_with_agent_credential_without_space_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'ingest.json'
            scope = {'space_id': 'space', 'agent_uuid': 'agent', 'endpoint': 'https://vision.test/v1/traces'}
            path.write_text(json.dumps({**scope, 'base_url': 'https://vision.test', 'agent_credential': 'aiv-agent-test'}))
            response = io.BytesIO(json.dumps({'code': 0, 'data': {**scope, 'token': 'short-token', 'expires_in': 900}}).encode())
            with patch.object(runtime.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = response
                _, token = runtime.token_for(path)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.headers['Authorization'], 'Bearer aiv-agent-test')
                self.assertEqual(json.loads(request.data), {'agent_uuid': 'agent'})
                self.assertEqual(token['token'], 'short-token')
            self.assertNotIn('aiv-agent-test', path.with_name('ingest-token.json').read_text())

    def test_cross_origin_download_rejected_before_request(self):
        with patch.object(installer,'request_bytes') as request:
            with self.assertRaises(installer.ConnectError):
                installer.download({'url':'https://other.test/binary','sha256':'a'*64},Path('/unused'),'https://vision.test')
            request.assert_not_called()

    def test_checksum_failure_does_not_replace_file(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'binary';file.write_bytes(b'original')
            with patch.object(installer,'request_bytes',return_value=b'broken'):
                with self.assertRaises(installer.ConnectError):
                    installer.download({'url':'https://vision.test/api/binary','sha256':'a'*64},file,'https://vision.test')
            self.assertEqual(file.read_bytes(),b'original')

    def test_archive_cannot_escape_or_install_symlinks(self):
        for name,kind in [('package/../../outside',tarfile.REGTYPE),('package/link',tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                archive=Path(temp)/'package.tgz'
                with tarfile.open(archive,'w:gz') as output:
                    info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd'
                    output.addfile(info,io.BytesIO(b''))
                with self.assertRaises(installer.ConnectError): installer.unpack_package(archive,Path(temp)/'unpacked')

    def test_hook_environment_routes_all_signals_and_overrides_old_config(self):
        for agent,prefix in [('codex','CDXHOOKS'),('claude-code','CCHOOKS'),('kimi','KIMI')]:
            with patch.dict(os.environ, {
                'AIVISION_ANT_USERNAME': 'legacy',
                'LANGFUSE_ATTR_USERNAME': 'legacy',
                prefix+'_USERNAME': 'legacy',
            }):
                env=runtime.hook_env({'agent':agent,'product':'ActualProduct','endpoint':'https://vision.test/otlp/v1/traces','config_dir':'/path with spaces'}, {'token':'placeholder'})
            self.assertEqual(env[prefix+'_ENABLE_TELEMETRY'],'1')
            self.assertEqual(env[prefix+'_METRICS_ENDPOINT'],'https://vision.test/otlp/v1/metrics')
            self.assertNotIn('AIVISION_ANT_USERNAME', env)
            self.assertNotIn('LANGFUSE_ATTR_USERNAME', env)
            self.assertNotIn(prefix+'_USERNAME', env)
            for signal in ['traces','logs','metrics']:
                key=f'{prefix}_OTLP_{signal.upper()}_ENDPOINT' if agent=='kimi' else f'{prefix}_OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT'
                self.assertEqual(env[key],f'https://vision.test/otlp/v1/{signal}')

    def test_cached_token_avoids_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ingest.json'
            scope = {'space_id': 'space', 'agent_uuid': 'agent', 'endpoint': 'https://vision.test/v1/traces'}
            path.write_text(json.dumps(scope))
            path.with_name('ingest-token.json').write_text(json.dumps({**scope,'token':'test','valid_until':1e12}))
            with patch.object(runtime.urllib.request,'build_opener') as opener:
                _, token=runtime.token_for(path)
                self.assertEqual(token['token'],'test');opener.assert_not_called()

    def test_cache_from_another_agent_or_endpoint_is_not_used_during_outage(self):
        for changed in ('agent_uuid', 'endpoint', 'space_id'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                path = Path(temp)/'ingest.json'
                scope = {'space_id': 'space', 'agent_uuid': 'agent', 'endpoint': 'https://vision.test/v1/traces'}
                path.write_text(json.dumps({**scope, 'base_url': 'https://vision.test', 'space_key': 'test-key'}))
                path.with_name('ingest-token.json').write_text(json.dumps({**scope, changed: 'other', 'token': 'test', 'valid_until': 1e12}))
                with patch.object(runtime.urllib.request, 'build_opener', side_effect=OSError('Offline')):
                    with self.assertRaises(OSError):
                        runtime.token_for(path)


if __name__=='__main__': unittest.main()
