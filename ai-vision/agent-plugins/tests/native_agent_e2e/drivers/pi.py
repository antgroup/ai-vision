"""Run official Pi RPC processes with the production observ-pi extension."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

try:
    from ..models import AgentResult, CaseContext
    from ._process import _join_readers, _kill_tree, _launch, _run, PIPE_DRAIN_SECONDS
except ImportError:
    from models import AgentResult, CaseContext
    from drivers._process import _join_readers, _kill_tree, _launch, _run, PIPE_DRAIN_SECONDS


def _loopback(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _environment(ctx: CaseContext, home: Path, agent: Path) -> dict[str, str]:
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TMP', 'TEMP', 'TMPDIR'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(HOME=str(home), USERPROFILE=str(home), PI_CODING_AGENT_DIR=str(agent),
               OBSERV_PI_STATE_DIR=str(ctx.out/'pi-state'), NO_PROXY='127.0.0.1,localhost,::1',
               no_proxy='127.0.0.1,localhost,::1', AGENT_ENABLE_TELEMETRY='true',
               OBSERV_PI_COLLECTION_MODE='full', PI_OFFLINE='1', NATIVE_E2E_PI_API_KEY='local-fake-key',
               OTEL_EXPORTER_OTLP_HEADERS=','.join(f'{key}={value}' for key, value in ctx.headers.items()))
    if ctx.resource.get('ant.username'):
        env['AIVISION_ANT_USERNAME']=str(ctx.resource['ant.username'])
    return env


class _Rpc:
    def __init__(self, command: list[str], ctx: CaseContext, env: dict[str, str], deadline: float):
        self.process, self.job = _launch(command, cwd=ctx.workspace, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding='utf-8', bufsize=1)
        self.deadline = deadline
        self.pending: queue.Queue = queue.Queue()
        self.events: list[dict] = []
        self.stderr: list[str] = []
        self.sequence = 0
        self._threads = [threading.Thread(target=self._read_stdout, daemon=True),
                         threading.Thread(target=self._read_stderr, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                try:
                    value=json.loads(line)
                except json.JSONDecodeError:
                    self.stderr.append(line)
                    continue
                self.pending.put(value)
        finally:
            self.process.stdout.close()
            self.pending.put(None)

    def _read_stderr(self):
        try:
            self.stderr.extend(self.process.stderr)
        finally:
            self.process.stderr.close()

    def send(self, kind: str, **payload) -> str:
        self.sequence += 1
        request_id=f'pi-e2e-{self.sequence}'
        self.process.stdin.write(json.dumps(dict(type=kind,id=request_id,**payload),ensure_ascii=False)+'\n')
        self.process.stdin.flush()
        return request_id

    def next(self) -> dict:
        remaining=self.deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Pi RPC exceeded the case deadline')
        try:
            event=self.pending.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError('Pi RPC exceeded the case deadline') from error
        if event is None:
            raise RuntimeError('Pi RPC closed before the operation completed')
        self.events.append(event)
        if event.get('type') == 'response' and event.get('success') is False:
            raise RuntimeError(f"Pi RPC rejected {event.get('command')}: {event.get('error', 'unknown')}")
        return event

    def request(self, kind: str, **payload) -> dict:
        request_id=self.send(kind,**payload)
        while True:
            event=self.next()
            if event.get('type') == 'response' and event.get('id') == request_id:
                return event.get('data') or {}

    def prompt(self, text: str, cancel: bool):
        self.send('prompt',message=text)
        aborted=False
        while True:
            event=self.next()
            update=event.get('assistantMessageEvent') or {}
            if cancel and not aborted and event.get('type') == 'message_update' and update.get('type') == 'text_delta' and update.get('delta'):
                self.send('abort')
                aborted=True
            if event.get('type') == 'agent_end':
                if cancel and not aborted:
                    raise RuntimeError('Pi completed before the intended stream cancellation')
                return aborted

    def close(self) -> int:
        try:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    # The native process may already have exited after rejecting RPC.
                    pass
            try:
                code = self.process.wait(timeout=max(0, min(15, self.deadline-time.monotonic())))
                _join_readers(self._threads,
                    min(PIPE_DRAIN_SECONDS, max(0, self.deadline-time.monotonic())))
                if any(thread.is_alive() for thread in self._threads):
                    code = 124
            except subprocess.TimeoutExpired:
                code = 124
        finally:
            try:
                _kill_tree(self.process, self.job)
            finally:
                _join_readers(self._threads)
        return code


def run_case(ctx: CaseContext) -> AgentResult:
    if ctx.mode not in ('fake', 'nvidia'):
        raise ValueError('Pi mode must be fake or nvidia')
    if not _loopback(ctx.provider_url):
        raise ValueError('Pi provider must be loopback (NVIDIA uses the explicit forwarding proxy)')
    if not _loopback(ctx.collector_url):
        raise ValueError('Pi acceptance collector must be loopback')
    extension=ctx.repo/'ai-vision/agent-plugins/pi/observ-pi/index.js'
    if not extension.is_file():
        raise ValueError('Build the production observ-pi package before native acceptance')
    ctx.out.mkdir(parents=True,exist_ok=True)
    agent=ctx.out/'pi-agent'
    agent.mkdir(exist_ok=False)
    home=ctx.out/'pi-home'
    home.mkdir()
    ctx.workspace.mkdir(parents=True,exist_ok=True)
    env=_environment(ctx,home,agent)
    provider='nvidia' if ctx.mode == 'nvidia' else 'native-e2e'
    model_config={'baseUrl':ctx.provider_url.rstrip('/'),'api':'openai-completions',
                  'apiKey':'$NATIVE_E2E_PI_API_KEY',
                  'models':[{'id':ctx.model,'reasoning':False,'input':['text'],'contextWindow':32768,
                             'maxTokens':4096,'compat':{'supportsStore':False,'supportsDeveloperRole':False,
                             'supportsReasoningEffort':False,'supportsUsageInStreaming':True,
                             'supportsStrictMode':False,'maxTokensField':'max_tokens'}}]}
    if ctx.mode == 'nvidia' and ctx.model.startswith('z-ai/glm-'):
        model_config['models'][0]['reasoning']=True
        model_config['models'][0]['compat'].update(thinkingFormat='chat-template',
                                                 chatTemplateKwargs={'enable_thinking':False})
    (agent/'models.json').write_text(json.dumps({'providers':{provider:model_config}},indent=2)+'\n')
    (agent/'settings.json').write_text(json.dumps({'autoCompaction':{'enabled':False},'retry':{'enabled':False}})+'\n')
    observer={'enableTelemetry':True,'collectionMode':'full','logDir':str(ctx.out/'pi-plugin-logs'),
              'otelExporter':{'resourceAttributes':ctx.resource,'metricsTemporalityPreference':'delta'},
              **{name:{'enabled':True,'endpoint':ctx.collector_url.rstrip('/')+'/v1/'+signal}
                 for name,signal in [('tracesConfig','traces'),('metricsConfig','metrics'),('eventsConfig','logs')]}}
    (agent/'observ-pi.json').write_text(json.dumps(observer,indent=2)+'\n')
    deadline=time.monotonic()+ctx.timeout_seconds
    try:
        version_result=_run([*ctx.runtime,'--version'],cwd=ctx.workspace,env=env,text=True,
                                      capture_output=True,timeout=min(30,ctx.timeout_seconds))
    except subprocess.TimeoutExpired:
        return AgentResult(124,None,'','',{'error':'Pi version check exceeded the case deadline'})
    version=version_result.stdout.strip()
    if version_result.returncode or not re.fullmatch(r'\d+\.\d+\.\d+',version):
        return AgentResult(version_result.returncode or 1,None,version,'',{'error':'Could not determine official Pi version'})
    base=[*ctx.runtime,'--mode','rpc','--provider',provider,'--model',ctx.model,'--tools','read,bash',
          '--no-extensions','--no-skills','--no-prompt-templates','--no-context-files','--no-themes','--offline','-e',str(extension),
          '--session-dir',str(ctx.out/'sessions')]
    if ctx.mode == 'nvidia':
        base += ['--thinking','off']
    events=[]
    stderr=[]
    state={}
    messages=[]
    error=None
    exit_code=0
    cancelled=False
    turns=0
    processes=0
    resumed=None
    for prompt in [ctx.prompt,*([ctx.followup_prompt] if ctx.followup_prompt else [])]:
        command=list(base)
        if turns:
            session_file=state.get('sessionFile')
            if not session_file or not Path(session_file).resolve().is_relative_to(ctx.out.resolve()):
                error='Pi did not return an isolated resumable native session file'
                exit_code=1
                break
            command += ['--session',session_file]
        rpc=_Rpc(command,ctx,env,deadline)
        processes += 1
        try:
            initial=rpc.request('get_state')
            if turns and initial.get('sessionId') != state.get('sessionId'):
                raise RuntimeError('Pi resumed a different physical session')
            if turns:
                resumed=initial.get('sessionId')
            cancelled=rpc.prompt(prompt,ctx.scenario == 'cancel')
            state=rpc.request('get_state')
            if state.get('isStreaming'):
                raise RuntimeError('Pi still reports active streaming after agent_end')
            messages=rpc.request('get_messages').get('messages',[])
            assistants=[message for message in messages if message.get('role') == 'assistant']
            if assistants and assistants[-1].get('stopReason') == 'error':
                raise RuntimeError(assistants[-1].get('errorMessage') or 'Pi assistant ended with an error')
            turns += 1
        except (RuntimeError,TimeoutError,BrokenPipeError) as failure:
            error=str(failure)
            exit_code=124 if isinstance(failure,TimeoutError) else 1
        finally:
            code=rpc.close()
            exit_code=exit_code or code
            events.extend(rpc.events)
            stderr.extend(rpc.stderr)
        if exit_code:
            break
    assistants=[message for message in messages if message.get('role') == 'assistant']
    output='\n'.join(block.get('text','') for block in (assistants[-1].get('content',[]) if assistants else []) if block.get('type') == 'text')
    # Never persist credentials, process environments, or native auth/config.
    details={'runtime':'pi','mode':ctx.mode,'model':ctx.model,'turns_completed':turns,'processes':processes,
             'resumed_session_id':resumed,'cancelled':cancelled,'rpc_events':events,'messages':messages,
             'stderr':''.join(stderr),**({'error':error} if error else {})}
    return AgentResult(exit_code,state.get('sessionId'),version,output,details)
