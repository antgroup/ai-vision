import unittest
from unittest.mock import Mock, patch
from run_suite import load_targets, safe, PRIVATE, validate_mode, refresh_ingest, DashboardSessions, execute_case
import httpx
import platform_verify
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace


class RunnerTests(unittest.TestCase):
    def tearDown(self):
        PRIVATE.clear()

    def test_default_fake_matrix_adds_only_opencode_retry_and_rejects_unsupported_pairs(self):
        import run_suite
        cases = run_suite.choose_cases(run_suite.APPS)
        self.assertEqual(len(cases), 49)
        self.assertEqual(len(set(cases)), 49)
        self.assertEqual(run_suite.VERSIONS.get('kimi'), '2.1.1')
        self.assertEqual(run_suite.PRODUCTS.get('kimi'), 'KimiCLI')
        self.assertEqual(run_suite.DRIVERS.get('kimi'), 'kimi')
        self.assertEqual({scenario for agent, scenario in cases if agent == 'kimi'}, set(run_suite.SCENARIOS))
        for app, version in (('dsh01', '0.1.7-rc.2'), ('dsh02', '0.2.0-rc.2')):
            self.assertEqual(run_suite.VERSIONS.get(app), version)
            self.assertEqual(run_suite.PRODUCTS.get(app), 'DeepSeekHarness')
            self.assertEqual({scenario for agent, scenario in cases if agent == app}, set(run_suite.SCENARIOS))
        self.assertIn(('opencode', 'api-retry'), cases)
        self.assertNotIn(('pi', 'api-retry'), cases)
        self.assertEqual(run_suite.choose_cases(['opencode'], 'api-retry'), [('opencode', 'api-retry')])
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(len(run_suite.choose_cases([a for a in run_suite.APPS if a!='kimi'], 'tool-loop,multi-turn,stream', mode='nvidia')), 15)
        with self.assertRaisesRegex(ValueError,'Kimi.*fake'):
            run_suite.choose_cases(['kimi'],'tool-loop',mode='nvidia')
        for apps, scenarios in ((['pi'], 'api-retry'), (['pi','opencode'], 'api-retry'), ([], None)):
            with self.subTest(apps=apps, scenarios=scenarios), self.assertRaises(ValueError):
                run_suite.choose_cases(apps, scenarios)
        with self.assertRaises(ValueError):
            run_suite.choose_cases(['opencode'], 'api-retry', mode='nvidia')

    def test_platform_readback_has_a_bounded_ingestion_wait(self):
        from models import AgentResult
        provider = SimpleNamespace(url='http://127.0.0.1:1/v1', lock=threading.Lock(), requests=[])
        capture = SimpleNamespace(url='http://127.0.0.1:2', lock=threading.Lock(), exports=[])
        def manager(value):
            result = Mock()
            result.__enter__ = Mock(return_value=value)
            result.__exit__ = Mock(return_value=False)
            return result
        driver = SimpleNamespace(run_case=lambda ctx: AgentResult(0, 'session', '1.0.2', 'answer', {}))
        target = {'endpoint': 'http://127.0.0.1:3'}
        sessions = SimpleNamespace(refresh=lambda value: value)
        with tempfile.TemporaryDirectory() as directory, \
             patch('run_suite.FakeProvider', return_value=manager(provider)), \
             patch('run_suite.Capture', return_value=manager(capture)), \
             patch('run_suite.refresh_ingest', side_effect=lambda value: value), \
             patch('run_suite.time.sleep'), \
             patch('run_suite.verify_case', return_value={'verified': True, 'checks': {}, 'failures': []}), \
             patch('run_suite.importlib.import_module', return_value=driver), \
             patch.object(platform_verify, 'verify_platform', return_value={'verified': True, 'checks': {}, 'failures': []}) as readback:
            result = execute_case(Path(directory), Path(directory)/'case', 'pi', 'tool-loop', ['pi'],
                                  target=target, dashboard_sessions=sessions)
        self.assertTrue(result['platform_verified'])
        self.assertEqual(readback.call_args.kwargs, {'poll_seconds': 10})

    def test_all_native_apps_and_modes_require_the_complete_receipt_line(self):
        from models import AgentResult
        from run_suite import VERSIONS
        for app in ('pi', 'hermes', 'opencode', 'dsh01', 'dsh02'):
            for mode in ('fake', 'nvidia'):
                with self.subTest(app=app, mode=mode), tempfile.TemporaryDirectory() as directory:
                    provider = SimpleNamespace(url='http://127.0.0.1:1/v1', lock=threading.Lock(), requests=[])
                    capture = SimpleNamespace(url='http://127.0.0.1:2', lock=threading.Lock(), exports=[])
                    def manager(value):
                        result = Mock()
                        result.__enter__ = Mock(return_value=value)
                        result.__exit__ = Mock(return_value=False)
                        return result
                    observed = []
                    def run(context):
                        observed.append(context)
                        return AgentResult(0, 'session', VERSIONS[app], '', {})
                    driver = SimpleNamespace(run_case=run)
                    with patch('run_suite.FakeProvider', return_value=manager(provider)) as provider_factory, patch('run_suite.Capture', return_value=manager(capture)), patch('run_suite.time.sleep'), patch('run_suite.verify_case', return_value={'verified': True, 'checks': {}, 'failures': []}) as oracle, patch('run_suite.importlib.import_module', return_value=driver) as modules:
                        execute_case(Path(directory), Path(directory)/'case', app, 'multi-turn', [app], mode=mode)
                    self.assertEqual(provider_factory.call_args.kwargs.get('length_continuation', False),
                                     app == 'hermes' and mode == 'fake')
                    self.assertEqual(oracle.call_args.kwargs.get('mode'), mode)
                    context = observed[0]
                    self.assertEqual(context.app, app)
                    self.assertEqual(context.runtime_version, VERSIONS[app])
                    self.assertEqual(modules.call_args.args, ('drivers.dsh' if app.startswith('dsh') else 'drivers.' + app,))
                    self.assertEqual(context.resource['ant.agent.type'], 'dsh' if app.startswith('dsh') else app)
                    value = (context.workspace/'receipt.txt').read_text().strip().removeprefix('receipt: ')
                    for prompt in (context.prompt, context.followup_prompt):
                        self.assertIn('complete single line', prompt)
                        self.assertIn("'receipt: '", prompt)
                        self.assertIn('verbatim', prompt)
                        self.assertNotIn(value, prompt, 'the model must obtain the random receipt through its real tool')

    def test_platform_inventory_cannot_silently_fall_back_to_capture_only(self):
        with self.assertRaises(ValueError):
            load_targets({}, ['pi'])
        with self.assertRaises(ValueError):
            load_targets({'pi': {'endpoint': 'https://external.example', 'data_backend': 'oceanbase'}}, ['pi'])

    def test_credentials_never_enter_artifact_or_error_output(self):
        target = {'endpoint':'http://127.0.0.1:5184','collector_endpoint':'http://127.0.0.1:4328',
                  'agent_uuid':'agent','agent_product':'Pi','space_id':'space','data_backend':'oceanbase',
                  'space_key':'space-value','collector_headers':{'Authorization':'Bearer ingest-value'},
                  'dashboard_headers':{'Cookie':'auth=cookie-value'}}
        self.assertEqual(load_targets({'pi':target}, ['pi']), {'pi':target})
        result = safe({'cookie':'auth=cookie-value','error':'space-value Bearer ingest-value auth=cookie-value'})
        self.assertEqual(result, {'cookie':'[REDACTED]','error':'[REDACTED] [REDACTED] [REDACTED]'})

    def test_live_model_suite_is_opt_in_and_never_runs_on_ci(self):
        with self.assertRaises(ValueError):
            validate_mode('nvidia', ['tool-loop'], {'GITHUB_ACTIONS':'true'})
        with self.assertRaises(ValueError):
            validate_mode('nvidia', ['zero-usage'], {})
        validate_mode('fake', ['zero-usage'], {'GITHUB_ACTIONS':'true'})
        validate_mode('nvidia', ['tool-loop','multi-turn','stream'], {})

    def test_short_lived_ingest_is_renewed_and_scope_mismatch_fails_closed(self):
        target={'endpoint':'http://127.0.0.1:5184','space_key':'key','space_id':'space',
                'agent_uuid':'agent','agent_product':'Pi','collector_headers':{'Authorization':'old'}}
        response=Mock(status_code=200)
        response.json.return_value={'code':0,'data':{'space_id':'space','agent_uuid':'agent',
            'agent_product':'Pi','token':'renewed','token_type':'Bearer'}}
        post=Mock(return_value=response)
        refreshed=refresh_ingest(target, post=post)
        self.assertEqual(refreshed['collector_headers']['Authorization'],'Bearer renewed')
        self.assertEqual(target['collector_headers']['Authorization'],'old')
        self.assertIn('renewed',PRIVATE)
        response.json.return_value['data']['agent_uuid']='other'
        with self.assertRaises(ValueError):
            refresh_ingest(target,post=post)

    def test_shared_dashboard_session_rotates_latest_cookie_without_replaying_old_token(self):
        requests=[]
        now=[0]
        def transport(request):
            requests.append(request)
            if request.url.path.endswith('/csrf'):
                return httpx.Response(200,json={'code':0},headers={'Set-Cookie':'csrftoken=csrf-new; Path=/'})
            number=sum(r.url.path.endswith('/refresh') for r in requests)
            return httpx.Response(200,json={'code':0},headers=[
                ('Set-Cookie',f'aivision_access=access-{number}; Path=/'),
                ('Set-Cookie',f'aivision_refresh=refresh-{number}; Path=/api/v1/auth/refresh')])
        sessions=DashboardSessions(clock=lambda:now[0],client_factory=lambda:httpx.Client(transport=httpx.MockTransport(transport)))
        target={'endpoint':'http://127.0.0.1:5184','dashboard_headers':{'Cookie':'aivision_refresh=original; aivision_access=expired'}}
        first=sessions.refresh(target)
        self.assertIn('access-1',first['dashboard_headers']['Cookie'])
        self.assertEqual(sessions.refresh(first)['dashboard_headers'],first['dashboard_headers'])
        self.assertEqual(sessions.refresh({**target,'agent_uuid':'another'})['dashboard_headers'],first['dashboard_headers'])
        self.assertEqual(len(requests),2)
        now[0]=601
        second=sessions.refresh(target)
        self.assertIn('access-2',second['dashboard_headers']['Cookie'])
        self.assertEqual(sessions.refresh(first)['dashboard_headers'],second['dashboard_headers'])
        self.assertIn('aivision_refresh=refresh-1',requests[-1].headers['Cookie'])
        self.assertNotIn('original',requests[-1].headers['Cookie'])
        self.assertEqual(requests[-1].headers['X-CSRFToken'],'csrf-new')
        self.assertEqual(safe('access-2 refresh-2'),'[REDACTED] [REDACTED]')

    def test_dashboard_refresh_rejection_is_not_replaced_with_a_fake_session(self):
        def transport(request):
            return httpx.Response(200,headers={'Set-Cookie':'csrftoken=csrf; Path=/'}) if request.url.path.endswith('/csrf') else httpx.Response(401,json={'code':4001})
        sessions=DashboardSessions(client_factory=lambda:httpx.Client(transport=httpx.MockTransport(transport)))
        with self.assertRaises(ValueError):
            sessions.refresh({'endpoint':'http://127.0.0.1:5184','dashboard_headers':{'Cookie':'aivision_refresh=bad'}})
        self.assertEqual(sessions.sessions,{})

    def test_native_or_oracle_exception_retains_original_facts_without_false_completion(self):
        provider=SimpleNamespace(url='http://127.0.0.1:1/v1',lock=threading.Lock(),requests=[{'original':'request'}])
        capture=SimpleNamespace(url='http://127.0.0.1:2',lock=threading.Lock(),exports=[{'original':'export'}])
        for failing in ('native','oracle'):
            with self.subTest(failing=failing),tempfile.TemporaryDirectory() as directory:
                provider_context=Mock()
                provider_context.__enter__=Mock(return_value=provider)
                provider_context.__exit__=Mock(return_value=False)
                capture_context=Mock()
                capture_context.__enter__=Mock(return_value=capture)
                capture_context.__exit__=Mock(return_value=False)
                driver=Mock()
                if failing=='native': driver.run_case.side_effect=RuntimeError('controlled native failure')
                else:
                    from models import AgentResult
                    driver.run_case.return_value=AgentResult(0,'session','1.0.2','answer',{})
                with patch('run_suite.FakeProvider',return_value=provider_context),patch('run_suite.Capture',return_value=capture_context),patch('run_suite.time.sleep'),patch('run_suite.verify_case',side_effect=RuntimeError('controlled oracle failure')),patch('run_suite.importlib.import_module',return_value=driver):
                    row=execute_case(Path(directory),Path(directory)/'case','pi','tool-loop',['pi'])
                self.assertFalse(row['verified'])
                report=json.loads((Path(directory)/'case/evidence.json').read_text())
                self.assertEqual(report['requests'],provider.requests)
                self.assertEqual(report['exports'],capture.exports)
                self.assertIn('source.oracle_exception',report['source']['failures'])
                if failing=='native':
                    self.assertIsNone(report['result']['session_id'])
                    self.assertIsNone(report['result']['exit_code'])
