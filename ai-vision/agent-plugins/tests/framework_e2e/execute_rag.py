"""Run real LangChain/Deep Agents + official tracing SDKs with fake models only.

Each CLI invocation owns one SDK exporter and closes all request tasks before
flush/shutdown. No provider configuration file or model credential is read.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
from contextlib import contextmanager, asynccontextmanager
from copy import deepcopy
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk
from rag_fixture import RAGFixture
from sdk_transport import CaptureForwarder, Ledger, remember_private, safe, write_json

SCENARIOS=('success','no-results','retrieval-error','retry','changed-corpus','zero-usage','unknown-usage','stream','cancel','concurrent','tool-exception')
PROMPT='For tenant orion, retrieve its storage policy and calculate retained storage with daily storage of 10 GiB. Return the calculation tool JSON with its actual citations and receipt. Delegate to researcher when available.'
FOLLOWUP='Follow up: return the previous answer with the same receipt without tools.'
# Application input recorded before SDK serialization. The verifier owns a
# separate literal oracle and never imports this fixture.
METADATA_SAMPLE = {
    'text': '中文 🧪', 'numeric_string': '42', 'boolean_string': 'true',
    'null_string': 'null', 'quoted_string': '"quoted"', 'empty_string': '',
    'number': 42, 'boolean': True, 'object': {'nested': None, 'value': '42'},
    'array': ['x', 2, False, None], 'null_value': None,
}



def clear_environment():
    prefixes=('OPENAI_','ANTHROPIC_','AZURE_OPENAI_','GOOGLE_API_','GEMINI_','OTEL_','LANGFUSE_','LANGCHAIN_','LANGSMITH_','AIVISION_OTLP_')
    removed=[]
    for key in list(os.environ):
        if key.startswith(prefixes):
            removed.append(key); os.environ.pop(key)
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        os.environ.pop(key,None)
    os.environ['NO_PROXY']=os.environ['no_proxy']='127.0.0.1,localhost,::1'
    return sorted(removed)


def message_record(message):
    result={'type':message.type,'content':deepcopy(message.content)}
    for key in ('name','id','tool_call_id','status','tool_calls','usage_metadata','response_metadata'):
        value=getattr(message,key,None)
        if value is not None: result[key]=deepcopy(value)
    return result


def usage_totals(rows):
    completed=[row for row in rows if row['kind']=='model_completed']
    return {key:sum(row['usage'][key] for row in completed)
            if completed and all(isinstance(row.get('usage'),dict) and row['usage'].get(key) is not None for row in completed)
            else None for key in ('input_tokens','output_tokens','total_tokens')}


def close_resources(sdk,capture):
    try:
        if sdk is not None: sdk.close()
    finally: capture.close()


def repo_path(value=None):
    candidates=[Path(value)] if value else list(Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate/'ai-vision/agent-plugins').is_dir(): return candidate.resolve()
    raise ValueError('Pass --repo with the checkout containing ai-vision/agent-plugins')


def load_helper(repo,relative,name):
    spec=importlib.util.spec_from_file_location(name,repo/relative)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_agent(framework,sdk,fixture):
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver
    if framework=='langchain':
        middleware=[]
        if sdk=='nemo-relay':
            from nemo_relay.integrations.langchain import NemoRelayMiddleware
            middleware=[NemoRelayMiddleware()]
        return create_agent(model=fixture.model('worker'),tools=fixture.tools(),middleware=middleware,
                            checkpointer=InMemorySaver(),name='rag-worker')
    from deepagents import create_deep_agent
    researcher={'name':'researcher','description':'Retrieve the tenant storage policy and calculate retained storage.',
                'model':fixture.model('worker'),'tools':fixture.tools(),
                'system_prompt':'Use retrieval then calculation. Return the actual calculation JSON.'}
    kwargs={'model':fixture.model('parent'),'tools':[], 'subagents':[researcher],
            'name':'rag-orchestrator','checkpointer':InMemorySaver(),
            'system_prompt':'Use researcher via task for the request. Preserve the exact child JSON result.'}
    if sdk=='nemo-relay':
        from nemo_relay.integrations.deepagents import add_nemo_relay_integration
        kwargs=add_nemo_relay_integration(kwargs,instrument_subagents=True)
    return create_deep_agent(**kwargs)


class AuditCallback(BaseCallbackHandler):
    run_inline=True
    def __init__(self,ledger,sid): self.ledger,self.sid=ledger,sid
    def on_tool_start(self,serialized,input_str,*,run_id,parent_run_id=None,inputs=None,**kwargs):
        self.ledger.emit('tool_start',session_id=self.sid,name=(serialized or {}).get('name'),
                         run_id=str(run_id),parent_run_id=str(parent_run_id) if parent_run_id else None,
                         arguments=inputs if inputs is not None else input_str)
    def on_tool_end(self,output,*,run_id,**kwargs):
        self.ledger.emit('tool_end',session_id=self.sid,run_id=str(run_id),
                         output=message_record(output) if hasattr(output,'type') else output)
    def on_llm_error(self,error,*,run_id,**kwargs):
        self.ledger.emit('model_error',session_id=self.sid,run_id=str(run_id),error_type=type(error).__name__)


class SDKSession:
    def __init__(self,sdk,framework,endpoint,target,repo,ledger):
        self.sdk,self.framework,self.endpoint,self.target,self.repo,self.ledger=sdk,framework,endpoint,target,repo,ledger
        self.client=None; self.runtime=None; self.exporter=None; self.lifecycle=None
    def start(self):
        if self.sdk=='langfuse':
            from langfuse import Langfuse
            self.client=Langfuse(public_key=self.target['agent_uuid'],secret_key=self.target['space_key'],
                                  base_url=self.endpoint,environment='aivision-e2e',flush_at=64,flush_interval=1,timeout=20)
            self.lifecycle=load_helper(self.repo,'ai-vision/agent-plugins/langfuse/streaming_lifecycle.py','rag_streaming_lifecycle')
        else:
            self.runtime=load_helper(self.repo,'ai-vision/agent-plugins/nvidia-nemo-relay/relay_tracing.py','rag_relay_tracing')
            auth='Basic '+base64.b64encode(f"{self.target['agent_uuid']}:{self.target['space_key']}".encode()).decode()
            remember_private(auth)
            os.environ.update(AIVISION_OTLP_ENDPOINT=self.endpoint+'/api/public/otel/v1/traces',
                              AIVISION_OTLP_AUTHORIZATION=auth,AIVISION_SERVICE_NAME='framework-rag-e2e')
            self.exporter=self.runtime.start_tracing()
    def config(self,sid,stream=False):
        if self.sdk=='langfuse':
            from langfuse.langchain import CallbackHandler
            cls=self.lifecycle.CancellationCallbackHandler if stream else CallbackHandler
            callback=cls(public_key=self.target['agent_uuid'])
        else:
            from nemo_relay.integrations.langchain import NemoRelayCallbackHandler
            from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsCallbackHandler
            cls=NemoRelayDeepAgentsCallbackHandler if self.framework=='deepagents' else NemoRelayCallbackHandler
            callback=self.runtime.cancellation_callback(cls) if stream else cls()
        return {'callbacks':[AuditCallback(self.ledger,sid),callback],
                'configurable':{'thread_id':sid},'metadata':{'session_id':sid},'tags':['agentic-rag-e2e'],
                'recursion_limit':50},callback
    @contextmanager
    def root(self,sid,phase,prompt):
        if self.sdk=='langfuse':
            from langfuse import propagate_attributes
            with propagate_attributes(session_id=sid,user_id='fake-rag-user',tags=['rag-e2e',phase]):
                with self.client.start_as_current_observation(name='rag-'+phase,as_type='agent',input={'request':prompt},metadata=METADATA_SAMPLE) as obs:
                    root={'trace_id':self.client.get_current_trace_id(),'output':None}
                    try: yield root
                    except BaseException as error:
                        obs.update(level='ERROR',status_message=type(error).__name__); raise
                    finally: obs.update(output=root['output'])
        else:
            with self.runtime.request_scope(sid,user_id='fake-rag-user',input={'request':prompt}) as root:
                yield root
    @contextmanager
    def embedding(self,op,inputs):
        if self.sdk=='langfuse':
            with self.client.start_as_current_observation(name=op,as_type='embedding',model='deterministic-fake-embedding',input=inputs) as obs:
                yield lambda output:obs.update(output=output)
        else:
            import nemo_relay
            # Scope IO is supplied through the public API. Relay 0.9.3 Embedder
            # projection omits numeric IO; application aliases retain actual IO.
            h=nemo_relay.scope.push(op,nemo_relay.ScopeType.Embedder,input=inputs,
                                  metadata={'aivision.embedding.input':json.dumps(inputs)})
            result={'output':None}
            status={}
            try: yield lambda output:result.update(output=output)
            except BaseException as error:
                status={'otel.status_code':'ERROR','otel.status_description':type(error).__name__}; raise
            finally:
                nemo_relay.scope.pop(h,output=result['output'],metadata={**status,'aivision.embedding.output':json.dumps(result['output'])})
    async def close_async(self):
        if self.sdk=='nemo-relay' and self.exporter is not None:
            try:
                # Relay metadata injectors belong to the live event loop. Drain
                # the official asynchronous queue before closing that loop.
                await self.runtime.stop_tracing_async()
                self.ledger.emit('lifecycle_flush',sdk=self.sdk)
                self.ledger.emit('lifecycle_shutdown',sdk=self.sdk)
            finally: self.exporter=None
    def close(self):
        if self.client is not None:
            try: self.client.flush(); self.ledger.emit('lifecycle_flush',sdk=self.sdk)
            finally: self.client.shutdown(); self.ledger.emit('lifecycle_shutdown',sdk=self.sdk)
        elif self.exporter is not None:
            self.runtime.stop_tracing()
            self.ledger.emit('lifecycle_flush',sdk=self.sdk); self.ledger.emit('lifecycle_shutdown',sdk=self.sdk)


@asynccontextmanager
async def plain_stream(stream):
    try: yield stream
    finally: await stream.aclose()


async def streamed(graph,config,callback,fixture,sdk,cancel):
    partial=[]; first=asyncio.Event()
    async def consume():
        stream=graph.astream({'messages':[{'role':'user','content':PROMPT}]},config,
                            stream_mode='messages',subgraphs=True)
        manager=(sdk.lifecycle if sdk.sdk=='langfuse' else sdk.runtime).managed_stream(stream,callback)
        async with manager as active:
            async for _namespace,chunk in active:
                message,_metadata=chunk
                if isinstance(message,(AIMessage,AIMessageChunk)) and message.content and not getattr(message,'tool_calls',None):
                    text=message.content if isinstance(message.content,str) else ''.join(b.get('text','') for b in message.content if isinstance(b,dict))
                    if text: partial.append(text); first.set()
    task=asyncio.create_task(consume())
    if not cancel:
        await task
        state=await graph.aget_state(config)
        return state.values, ''.join(partial),False
    try:
        waiter=asyncio.create_task(first.wait())
        done,_=await asyncio.wait({task,waiter},timeout=30,return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            waiter.cancel(); await asyncio.gather(waiter,return_exceptions=True)
            await task
            raise AssertionError('Cancel scenario completed before an observed partial final output')
        if waiter not in done:
            waiter.cancel(); await asyncio.gather(waiter,return_exceptions=True)
            raise TimeoutError('No final stream text observed within 30 seconds')
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        else: raise AssertionError('Cancelled stream did not propagate CancelledError')
        return None,''.join(partial),True
    finally:
        if not task.done():
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
        fixture.release_stream.set()


async def run_session(sdk,framework,scenario,run_id,index,session_sink=None,*,fixture_type=RAGFixture,
                      tenant="orion",prompt=PROMPT,session_id=None,followup_prompts=(FOLLOWUP,)):
    sid=session_id or f'rag-{framework}-{sdk.sdk}-{run_id}-{index}'
    fixture=fixture_type('success' if scenario=='concurrent' else scenario,receipt='receipt-'+uuid4().hex,
                       tenant=tenant,observe_embedding=sdk.embedding)
    session={'session_id':sid,'receipt':fixture.receipt,'tenant':tenant,'roots':[],
             'business_results':[],'fixture_expected':fixture.expected,'fixture_ledger':fixture.ledger}
    if session_sink is not None: session_sink.append(session)
    graph=build_agent(framework,sdk.sdk,fixture)
    async def turn(phase,prompt):
        streaming=scenario in ('stream','cancel')
        config,callback=sdk.config(sid,streaming)
        record={'phase':phase,'input':{'request':prompt},'start_ns':time.time_ns(),'status':'running','output':None}
        session['roots'].append(record)
        try:
            with sdk.root(sid,phase,prompt) as root:
                record['trace_id']=root['trace_id']
                sdk.ledger.emit('sdk_root',trace_id=root['trace_id'],session_id=sid,phase=phase)
                if streaming:
                    result,partial,cancelled=await streamed(graph,config,callback,fixture,sdk,scenario=='cancel')
                    if cancelled:
                        record.update(output={'partial_text':partial,'complete':False},status='cancelled')
                        root['output']=record['output']
                        session['business_results'].append({'phase':phase,'messages':[],'final':None,'partial_text':partial,'cancelled':True})
                        # Mark the actual application root cancellation. The graph
                        # task has already stopped and callback cleanup completed.
                        raise asyncio.CancelledError()
                else:
                    result=await graph.ainvoke({'messages':[{'role':'user','content':prompt}]},config)
                messages=[message_record(m) for m in result['messages']]
                final=json.loads(result['messages'][-1].content)
                record.update(output=final,status='completed'); root['output']=final
                session['business_results'].append({'phase':phase,'messages':messages,'final':final,'output':final})
        except asyncio.CancelledError:
            if record['status']!='cancelled': raise
        except Exception as error:
            record['status']='error'
            if scenario=='tool-exception' and isinstance(error,RuntimeError) and any(r['kind']=='tool_error' for r in fixture.ledger):
                session['expected_error']={'type':type(error).__name__,'message':str(error)}
                session['business_results'].append({'phase':phase,'messages':[],'final':None,'error':session['expected_error']})
            else: raise
        finally: record['end_ns']=time.time_ns()
    await turn('core',prompt)
    if scenario=='success':
        before=sum(row['kind'] in ('tool_result','tool_error') for row in fixture.ledger)
        for followup in followup_prompts:
            await turn('followup',followup)
        session['followup_no_repeated_tools']=sum(row['kind'] in ('tool_result','tool_error') for row in fixture.ledger)==before
    session['model_usage']=usage_totals(fixture.ledger)
    session['fixture_ledger']=fixture.ledger
    return session


def targets(sdk,platform):
    if not platform:
        return {'endpoint':None,'agent_uuid':'pk-fake-rag-'+sdk,'space_key':'sk-fake-rag-collector-only',
                'space_id':'collector-only','agent_product':'framework-rag-e2e'}
    raw=os.environ.get('AIVISION_E2E_TARGETS_JSON')
    if not raw: raise ValueError('--platform requires AIVISION_E2E_TARGETS_JSON')
    value=json.loads(raw)[sdk]
    for key in ('endpoint','agent_uuid','space_key','space_id','agent_product'):
        if not isinstance(value.get(key),str) or not value[key]: raise ValueError('Missing platform target field: '+key)
    parsed=urlsplit(value['endpoint'])
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Platform endpoint must be a plain HTTP(S) origin')
    remember_private(value['space_key']); remember_private(raw)
    return value


def public_identity(target):
    """Only non-secret identity fields may enter public SDK evidence."""
    keys = ('endpoint', 'agent_uuid', 'space_id', 'agent_product', 'data_backend', 'authenticated_user_id')
    return {**{key: target[key] for key in keys if key in target}, 'base_url': target.get('endpoint')}


def versions():
    names=('langchain','langchain-core','langgraph','deepagents','langfuse','nemo-relay','numpy','opentelemetry-sdk')
    result={'python':sys.version.split()[0]}
    for name in names:
        try: result[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: result[name]=None
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--framework',choices=('langchain','deepagents','lc','da'),required=True)
    parser.add_argument('--sdk',choices=('langfuse','nemo-relay'),required=True)
    parser.add_argument('--scenario',choices=SCENARIOS,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--repo',type=Path)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--platform',action='store_true'); mode.add_argument('--collector-only',action='store_true')
    args=parser.parse_args()
    args.framework={'lc':'langchain','da':'deepagents'}.get(args.framework,args.framework)
    if args.out.exists() and any(args.out.iterdir()): parser.error('--out must be a fresh empty directory')
    args.out.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'run_id':uuid4().hex[:12],'framework':args.framework,'sdk':args.sdk,'scenario':args.scenario,
            'versions':versions(),'sessions':[],'bounds':{'from_ms':time.time_ns()//1000000},'checks':{},'known_limits':[],
            'sdk_exports_path':'sdk-exports.json','ledger_path':'ledger.jsonl','platform_query_verified':False,
            'source_verified':False,'validation_scope':'application execution and SDK transport; independent verification required','status':'failed'}
    if args.sdk == 'langfuse':
        report['metadata_contract'] = 'python-sdk-json-types-v1'
    ledger=Ledger(args.out/'ledger.jsonl'); capture=None; sdk=None
    try:
        target=targets(args.sdk,args.platform)
        report['identity']=public_identity(target)
        report['cleared_environment_names']=clear_environment()
        capture=CaptureForwarder(target['endpoint'],ledger)
        sdk=SDKSession(args.sdk,args.framework,capture.start(),target,repo_path(args.repo),ledger)
        sdk.start()
        async def execute():
            try:
                if args.scenario=='concurrent':
                    tasks=[asyncio.create_task(run_session(sdk,args.framework,args.scenario,report['run_id'],i,report['sessions'])) for i in range(2)]
                    try: return await asyncio.gather(*tasks)
                    finally:
                        for task in tasks:
                            if not task.done(): task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)
                return [await run_session(sdk,args.framework,args.scenario,report['run_id'],0,report['sessions'])]
            finally: await sdk.close_async()
        asyncio.run(execute())
        checks=report['checks']
        checks['roots_recorded']=all(s['roots'] and all(r.get('trace_id') for r in s['roots']) for s in report['sessions'])
        checks['application_completed']=all(r['status'] in (('error',) if args.scenario=='tool-exception' else ('completed','cancelled')) for s in report['sessions'] for r in s['roots'])
        if args.scenario=='success': checks['checkpoint_no_repeated_tools']=all(s['followup_no_repeated_tools'] for s in report['sessions'])
        if args.scenario=='concurrent':
            checks['independent_sessions']=len({s['session_id'] for s in report['sessions']})==2
            checks['independent_receipts']=len({s['receipt'] for s in report['sessions']})==2
        if args.sdk=='nemo-relay' and args.scenario=='cancel':
            report['known_limits'].append({'id':'NR-002','description':'Relay 0.9.3 scopes are closed through request-owned public callbacks. Native partial output, complete cancellation usage and TTFT remain unavailable; native task end can precede Python subgraph cleanup.'})
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':safe(str(error))}
    finally:
        try:
            if capture is not None: close_resources(sdk,capture)
        except BaseException as error:
            report['cleanup_error']={'type':type(error).__name__,'message':safe(str(error))}
        report['bounds']['to_ms']=time.time_ns()//1000000
        if capture is not None:
            report['transport']=capture.summary(); write_json(args.out/'sdk-exports.json',capture.exports)
            captured_roots={span['trace_id']:span for export in capture.exports for span in export.get('spans',[]) if not span.get('parent_span_id')}
            for session in report['sessions']:
                for root in session['roots']:
                    root['application_status']=root['status']
                    root['status']=captured_roots.get(root.get('trace_id'),{}).get('status',{})
            report['checks']['sdk_ingestion_accepted']=report['transport']['ingestion_http_accepted']
        else: write_json(args.out/'sdk-exports.json',[])
        good=bool(report['checks']) and all(report['checks'].values()) and not report.get('error') and not report.get('cleanup_error')
        report['status']=('known_limitation' if report['known_limits'] else 'passed') if good else 'failed'
        write_json(args.out/'report.json',report)
    print(json.dumps({'status':report['status'],'framework':args.framework,'sdk':args.sdk,'scenario':args.scenario,'out':str(args.out),'error':report.get('error')}))
    return 0 if report['status'] in ('passed','known_limitation') else 1

if __name__=='__main__': raise SystemExit(main())
