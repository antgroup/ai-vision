"""Pi driver process-boundary tests; actual Pi acceptance belongs to the suite."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

from models import CaseContext
from drivers.pi import _Rpc, run_case
from _process_test_helpers import assert_posix_process_stopped


HOST = r'''
import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('1.0.2')
    raise SystemExit()
agent = Path(os.environ['PI_CODING_AGENT_DIR'])
session_file=agent.parent/'sessions'/'physical-session.jsonl'
session_file.parent.mkdir(parents=True,exist_ok=True)
session_file.write_text('native session fixture')
settings = json.loads((agent/'settings.json').read_text())
models = json.loads((agent/'models.json').read_text())
observer = json.loads((agent/'observ-pi.json').read_text())
provider = next(iter(models['providers'].values()))
assert provider['baseUrl'] == 'http://127.0.0.1:2222/v1'
assert settings['autoCompaction'] == {'enabled': False}
assert not os.environ.get('OPENAI_API_KEY')
assert not os.environ.get('NVIDIA_API_KEY')
assert not os.environ.get('PIREPLAY_CAPTURE_ENABLED')
assert os.environ.get('AIVISION_ANT_USERNAME') == 'native-e2e-user'
if provider['models'][0]['id'] == 'z-ai/glm-5.3-flash':
    assert provider['apiKey'] == '$NATIVE_E2E_PI_API_KEY'
    assert provider['models'][0]['reasoning'] is True
    assert provider['models'][0]['compat']['thinkingFormat'] == 'chat-template'
    assert provider['models'][0]['compat']['chatTemplateKwargs'] == {'enable_thinking':False}
    assert sys.argv[sys.argv.index('--thinking')+1] == 'off'
assert observer['tracesConfig']['endpoint'] == 'http://127.0.0.1:3333/v1/traces'
assert observer['metricsConfig']['endpoint'] == 'http://127.0.0.1:3333/v1/metrics'
assert observer['otelExporter']['metricsTemporalityPreference'] == 'delta'
assert observer['eventsConfig']['endpoint'] == 'http://127.0.0.1:3333/v1/logs'
assert '-e' in sys.argv and sys.argv[sys.argv.index('-e')+1].endswith('observ-pi/index.js')
for line in sys.stdin:
    value=json.loads(line)
    command=value['type']
    data={}
    if command == 'get_state':
        data={'sessionId':'physical-session', 'sessionFile':str(session_file), 'isStreaming':False}
    elif command == 'get_messages':
        data={'messages':[{'role':'assistant','content':[{'type':'thinking','thinking':'private reasoning'}, {'type':'text','text':'physical receipt'}]}]}
    print(json.dumps({'type':'response','id':value.get('id'),'command':command,'success':True,'data':data}),flush=True)
    if command == 'prompt':
        print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':value['message']}], 'stopReason':'stop'}}),flush=True)
        print(json.dumps({'type':'agent_end','messages':[]}),flush=True)
'''


class PiDriverTests(unittest.TestCase):
    def context(self, root: Path, **changes):
        host=root/'rpc_host.py'
        host.write_text(HOST,encoding='utf-8')
        repo=root/'repo'
        extension=repo/'ai-vision/agent-plugins/pi/observ-pi/index.js'
        extension.parent.mkdir(parents=True)
        extension.write_text('test boundary',encoding='utf-8')
        workspace=root/'workspace'
        workspace.mkdir()
        values=dict(repo=repo,out=root/'out',workspace=workspace,runtime=(sys.executable,str(host)),
                    provider_url='http://127.0.0.1:2222/v1',collector_url='http://127.0.0.1:3333',
                    scenario='tool-loop',prompt='read fixture',timeout_seconds=8,
                    resource={'ant.username':'native-e2e-user'})
        values.update(changes)
        return CaseContext(**values)

    def test_isolated_process_config_and_native_rpc_output(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=self.context(Path(directory))
            with patch.dict(os.environ,{'OPENAI_API_KEY':'do-not-forward','PIREPLAY_CAPTURE_ENABLED':'true','PI_CODING_AGENT_DIR':'do-not-write'}):
                result=run_case(ctx)
            self.assertEqual(result.exit_code,0)
            self.assertEqual(result.session_id,'physical-session')
            self.assertEqual(result.version,'1.0.2')
            self.assertEqual(result.output,'physical receipt')
            self.assertNotIn('do-not-forward',json.dumps(result.details))
            self.assertEqual(result.details['turns_completed'],1)
            self.assertTrue((ctx.out/'pi-agent/models.json').exists())

    def test_two_prompts_share_one_physical_session(self):
        with tempfile.TemporaryDirectory() as directory:
            result=run_case(self.context(Path(directory),scenario='multi-turn',followup_prompt='reuse receipt'))
            self.assertEqual(result.exit_code,0)
            self.assertEqual(result.details['turns_completed'],2)
            self.assertEqual(result.session_id,'physical-session')
            self.assertEqual(result.details['processes'],2)
            self.assertEqual(result.details['resumed_session_id'],'physical-session')

    def test_fake_mode_rejects_nonlocal_provider_before_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=self.context(Path(directory),provider_url='https://example.com/v1')
            with self.assertRaisesRegex(ValueError,'loopback'):
                run_case(ctx)
            self.assertFalse(ctx.out.exists())

    def test_runtime_failure_is_not_returned_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=self.context(Path(directory))
            Path(ctx.runtime[1]).write_text("import sys\nprint('1.0.2') if '--version' in sys.argv else None\nraise SystemExit(0 if '--version' in sys.argv else 17)\n")
            result=run_case(ctx)
            self.assertNotEqual(result.exit_code,0)
            self.assertIsNone(result.session_id)

    def test_timeout_closes_rpc_with_descendants_holding_both_pipes(self):
        self._assert_owned_descendant_cleanup(parent_wait=10)

    def test_exited_rpc_parent_cannot_leave_inherited_pipes_open(self):
        self._assert_owned_descendant_cleanup(parent_wait=0)

    def _assert_owned_descendant_cleanup(self, parent_wait):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = self.context(root)
            receipt = root / 'child.pid'
            host = root / 'tree.py'
            host.write_text("import os,subprocess,sys,time\n"
                "from pathlib import Path\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                f"Path({str(receipt)!r}).write_text(str(child.pid))\n"
                f"time.sleep({parent_wait})\n")
            started = time.monotonic()
            rpc = _Rpc([sys.executable, str(host)], ctx, dict(os.environ), started + .2)
            outcome = []
            def close():
                try:
                    outcome.append(rpc.close())
                except BaseException as error:
                    outcome.append(error)
            closer = threading.Thread(target=close, daemon=True)
            try:
                until = time.monotonic() + 2
                while not receipt.exists() and time.monotonic() < until:
                    time.sleep(.01)
                self.assertTrue(receipt.exists(), 'host must spawn a real inherited-pipe child')
                child = int(receipt.read_text())
                closer.start()
                closer.join(timeout=1.5)
                self.assertFalse(closer.is_alive(), 'RPC shutdown exceeded its bounded cleanup')
                self.assertEqual(outcome, [124])
                if os.name == 'posix':
                    assert_posix_process_stopped(child, timeout=.5)
                else:
                    listing = subprocess.run(['tasklist', '/FI', f'PID eq {child}', '/NH', '/FO', 'CSV'],
                        capture_output=True, text=True, timeout=2)
                    self.assertNotIn(f'"{child}"', listing.stdout)
                self.assertFalse(any(thread.is_alive() for thread in rpc._threads))
            finally:
                for pid in (int(receipt.read_text()) if receipt.exists() else None, rpc.process.pid):
                    if pid is None:
                        continue
                    if os.name == 'posix':
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                            capture_output=True, timeout=2)
                if closer.ident:
                    closer.join(timeout=5)

    def test_version_timeout_returns_a_failed_receipt_after_killing_the_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = self.context(root, timeout_seconds=.5)
            receipt = root / 'version-child.pid'
            Path(ctx.runtime[1]).write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                f'Path({str(receipt)!r}).write_text(str(child.pid))\n'
                'print("1.0.2",flush=True)\ntime.sleep(10)\n')
            try:
                result = run_case(ctx)
                self.assertEqual(result.exit_code, 124)
                self.assertIn('version', result.details['error'])
                self.assertTrue(receipt.exists())
                if os.name == 'posix':
                    assert_posix_process_stopped(int(receipt.read_text()), timeout=.5)
            finally:
                if receipt.exists() and os.name == 'posix':
                    try:
                        os.kill(int(receipt.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_case_timeout_retains_partial_diagnostics_and_failed_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = self.context(root, timeout_seconds=1.5)
            receipt = root / 'rpc-child.pid'
            Path(ctx.runtime[1]).write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
                "if '--version' in sys.argv:\n    print('1.0.2'); raise SystemExit()\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                f'Path({str(receipt)!r}).write_text(str(child.pid))\n'
                'print("partial RPC diagnostic",file=sys.stderr,flush=True)\ntime.sleep(10)\n')
            try:
                result = run_case(ctx)
                self.assertEqual(result.exit_code, 124)
                self.assertEqual(result.details['turns_completed'], 0)
                self.assertIn('deadline', result.details['error'])
                self.assertIn('partial RPC diagnostic', result.details['stderr'])
                self.assertTrue(receipt.exists())
                if os.name == 'posix':
                    assert_posix_process_stopped(int(receipt.read_text()), timeout=.5)
            finally:
                if receipt.exists() and os.name == 'posix':
                    try:
                        os.kill(int(receipt.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_nvidia_proxy_uses_placeholder_auth_and_explicit_glm_thinking_off(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=self.context(Path(directory),mode='nvidia',model='z-ai/glm-5.3-flash')
            with patch.dict(os.environ,{'NVIDIA_API_KEY':'synthetic-private-key'}):
                result=run_case(ctx)
            self.assertEqual(result.exit_code,0)
            configs=''.join(path.read_text() for path in (ctx.out/'pi-agent').glob('*.json'))
            self.assertNotIn('synthetic-private-key',configs+json.dumps(result.details))


if __name__ == '__main__':
    unittest.main()
