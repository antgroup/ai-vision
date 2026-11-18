"""Real official Relay middleware and nested Deep Agents cancellation, no network."""
import asyncio
import calendar
from contextlib import asynccontextmanager
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import time
import unittest
from uuid import uuid4

import nemo_relay
from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsCallbackHandler, add_nemo_relay_integration
from pydantic import Field


class ParentModel(FakeMessagesListChatModel):
    model_name: str = 'cancel-parent'
    def bind_tools(self, tools, **kwargs): return self


class PausingModel(BaseChatModel):
    model_name: str = 'cancel-child'
    pause: bool = True
    lifecycle: list[tuple[str,int]] = Field(default_factory=list,exclude=True)
    model_stopped: asyncio.Event = Field(default_factory=asyncio.Event,exclude=True)
    cleanup_entered: asyncio.Event = Field(default_factory=asyncio.Event,exclude=True)
    cleanup_release: asyncio.Event | None = Field(default=None,exclude=True)
    @property
    def _llm_type(self): return 'fake-cancel-child'
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self, *args, **kwargs): raise AssertionError('Streaming path required')
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            self.lifecycle.append(('partial',time.time_ns()))
            yield ChatGenerationChunk(message=AIMessageChunk(content='visible partial'))
            if self.pause: await asyncio.Event().wait()
            self.lifecycle.append(('finished',time.time_ns()))
            yield ChatGenerationChunk(message=AIMessageChunk(content=' finished'))
        finally:
            self.cleanup_entered.set()
            if self.cleanup_release is not None:
                await self.cleanup_release.wait()
            self.lifecycle.append(('stopped',time.time_ns()))
            self.model_stopped.set()


@asynccontextmanager
async def baseline_stream(stream, callback):
    try: yield stream
    finally: await stream.aclose()


def timestamp_ns(event):
    # Compare the original RFC 3339 fractions without datetime's microsecond
    # truncation or a float conversion of Unix time.
    match = re.fullmatch(r'(.+?)(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', event['timestamp'])
    whole, fraction, offset = match.groups()
    stamp = datetime.fromisoformat(whole + offset)
    return calendar.timegm(stamp.utctimetuple()) * 1_000_000_000 + int((fraction or '').ljust(9, '0'))


class RelayCancellationTests(unittest.TestCase):
    def test_nested_cancellation_closes_real_agent_scopes_and_preserves_error(self):
        self.run_case(cancel=True)

    def test_normal_nested_completion_is_not_changed_or_duplicated(self):
        self.run_case(cancel=False)

    def test_cancellation_waits_for_actual_model_finalizer_before_business_scopes(self):
        self.run_case(cancel=True,controlled_cleanup=True)

    def test_synchronous_model_callbacks_preserve_official_behavior_without_loop(self):
        spec=importlib.util.spec_from_file_location('relay_sync_model_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        callback=runtime.cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
        callback.on_llm_start({},['answer'],run_id=uuid4())
        with self.assertRaises(NotImplementedError):
            callback.on_chat_model_start({},[],run_id=uuid4())
        self.assertFalse(callback.model_tasks)

    def test_early_break_closes_pending_scopes_as_incomplete(self):
        self.run_case(cancel=False, early_close=True)

    def test_explicit_generator_exit_is_not_replaced_by_scope_error(self):
        self.run_case(cancel=False, generator_exit=True)

    def test_sdk_cancelled_sibling_scopes_keep_both_end_orders_inside_request(self):
        # Characterize native tool cancellation separately from graph lifecycle.
        # A model generator finalizer is not the official worker scope boundary.
        for task_first in (True, False):
            with self.subTest(task_first=task_first):
                self.run_native_boundary_case(task_first)

    def test_sdk_generator_exit_has_no_native_guard_completion_ack(self):
        # NR002: the public Python Future is cancelled synchronously. Its SDK
        # done callback signals Tokio on a later loop turn; it exposes no native
        # guard completion acknowledgement. Hold actual native tool cleanup,
        # after the real worker callback stopped, to prove this SDK boundary.
        spec=importlib.util.spec_from_file_location('relay_native_ack_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        recordings=[];name='native-ack-'+uuid4().hex
        native_end_seen=native_loop=None
        def record(event):
            recordings.append((event.to_dict(),event.timestamp))
            if event.kind=='scope' and event.scope_category=='end' and event.name=='task':
                native_loop.call_soon_threadsafe(native_end_seen.set)
        async def execute():
            nonlocal native_end_seen,native_loop
            native_end_seen=asyncio.Event();native_loop=asyncio.get_running_loop()
            callback=NemoRelayDeepAgentsCallbackHandler();orchestrator,worker_run=uuid4(),uuid4()
            metadata={'lc_versions':{'deepagents':'0.7.21'},'lc_agent_name':'orchestrator'}
            worker_stopped,release_tool=asyncio.Event(),asyncio.Event();tool_tasks=[]
            original=GeneratorExit('consumer closed')
            nemo_relay.subscribers.register(name,record)
            try:
                with self.assertRaises(GeneratorExit) as caught:
                    with runtime.request_scope('native-ack',input={'request':'early-close'}):
                        callback.on_chain_start({}, {}, run_id=orchestrator,name='orchestrator',metadata=metadata)
                        async def tool(args):
                            tool_tasks.append(asyncio.current_task())
                            callback.on_chain_start({}, {}, run_id=worker_run,parent_run_id=orchestrator,
                                name='researcher',metadata={**metadata,'lc_agent_name':'researcher'})
                            callback.on_chain_error(original,run_id=worker_run)
                            worker_stopped.set()
                            await release_tool.wait()
                            raise original
                        native=asyncio.ensure_future(nemo_relay.tools.execute('task',{},tool))
                        await asyncio.wait_for(worker_stopped.wait(),5)
                        self.assertFalse(native.done())
                        native.cancel('graph closed')
                        try: await native
                        except asyncio.CancelledError:
                            self.assertTrue(native.done())
                            self.assertTrue(worker_stopped.is_set())
                            callback.on_chain_error(original,run_id=orchestrator)
                            # No loop yield occurs between the cancelled Future
                            # and the real request root pop.
                            raise original
                        else: self.fail('Native managed tool lost cancellation')
                self.assertIs(caught.exception,original)
            finally:
                # Let the SDK's actual cancel bridge drop the native guard.
                # Releasing the business continuation first would race its
                # GeneratorExit failure against native cancellation.
                await asyncio.wait_for(native_end_seen.wait(),5)
                release_tool.set()
                await asyncio.gather(*tool_tasks,return_exceptions=True)
                await nemo_relay.subscribers.flush_async()
                nemo_relay.subscribers.deregister(name)
        asyncio.run(execute())
        events=[event for event,_ in recordings]
        for event,native_timestamp in recordings:
            self.assertEqual(event['timestamp'],native_timestamp)
        start_events=[event for event in events if event.get('scope_category')=='start']
        starts={event['uuid']:event for event in start_events}
        self.assertEqual(len(start_events),len(starts))
        ends=[event for event in events if event.get('scope_category')=='end']
        self.assertEqual(set(starts),{event['uuid'] for event in ends})
        self.assertEqual(len(starts),len(ends))
        by_name={event['name']:event for event in ends}
        self.assertEqual(set(by_name),{'application-request','orchestrator','researcher','task'})
        root,orchestrator,worker,task=(by_name[name] for name in ('application-request','orchestrator','researcher','task'))
        self.assertEqual(worker['parent_uuid'],task['parent_uuid'])
        self.assertEqual(worker['parent_uuid'],orchestrator['uuid'])
        self.assertEqual(orchestrator['parent_uuid'],root['uuid'])
        self.assertEqual(task['category'],'tool')
        self.assertEqual(task['metadata']['otel.status_description'],'tool execution cancelled')
        for event in ends:
            self.assertEqual(event['metadata']['otel.status_code'],'ERROR')
            self.assertLessEqual(timestamp_ns(starts[event['uuid']]),timestamp_ns(event))
            self.assertLessEqual(timestamp_ns(starts[root['uuid']]),timestamp_ns(starts[event['uuid']]))
        self.assertLessEqual(timestamp_ns(worker),timestamp_ns(orchestrator))
        self.assertLessEqual(timestamp_ns(orchestrator),timestamp_ns(root))
        self.assertLessEqual(timestamp_ns(root),timestamp_ns(task))
        self.assertLess(events.index(root),events.index(task))
        print('Relay SDK NR002 native guard completion after closed Python request:',
              {'root_end':root['timestamp'],'native_task_end':task['timestamp'],
               'native_tail_ns':timestamp_ns(task)-timestamp_ns(root)})

    def run_native_boundary_case(self, task_first):
        path=Path(__file__).parents[1]/'relay_tracing.py'
        spec=importlib.util.spec_from_file_location('relay_native_boundary_test',path)
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        recordings=[]
        async def execute():
            task_end_seen,worker_end_seen=asyncio.Event(),asyncio.Event()
            loop=asyncio.get_running_loop()
            def record(event):
                data=event.to_dict();recordings.append((data,event.timestamp))
                if data.get('scope_category')=='end' and data['name']=='task':
                    loop.call_soon_threadsafe(task_end_seen.set)
                elif data.get('scope_category')=='end' and data['name']=='researcher':
                    loop.call_soon_threadsafe(worker_end_seen.set)
            name='native-boundary-'+uuid4().hex
            nemo_relay.subscribers.register(name,record)
            original_errors=[]
            try:
                with self.assertRaises(asyncio.CancelledError) as caught:
                    with runtime.request_scope('native-boundary',input={'request':'cancel'}):
                        callback=NemoRelayDeepAgentsCallbackHandler()
                        orchestrator,worker_run=uuid4(),uuid4()
                        metadata={'lc_versions':{'deepagents':'0.7.21'},'lc_agent_name':'orchestrator'}
                        callback.on_chain_start({}, {}, run_id=orchestrator,name='orchestrator',metadata=metadata)
                        started,closing,release_worker,worker_ended=(asyncio.Event() for _ in range(4))
                        release_tool=asyncio.Event();worker_tasks=[]
                        async def worker():
                            callback.on_chain_start({}, {}, run_id=worker_run,parent_run_id=orchestrator,
                                name='researcher',metadata={**metadata,'lc_agent_name':'researcher'})
                            started.set()
                            try: await asyncio.Event().wait()
                            except asyncio.CancelledError as error:
                                closing.set();await release_worker.wait()
                                # The real public callback records the actual scope end.
                                callback.on_chain_error(error,run_id=worker_run)
                                worker_ended.set()
                                raise
                        async def tool(args):
                            worker_task=asyncio.create_task(worker());worker_tasks.append(worker_task)
                            try: await asyncio.shield(worker_task)
                            except asyncio.CancelledError:
                                if worker_task.done():
                                    # Worker has stopped; tool cleanup remains cancellable.
                                    await release_tool.wait()
                                else:
                                    worker_task.cancel('client disconnected')
                                    await asyncio.gather(worker_task,return_exceptions=True)
                                raise
                        native=asyncio.ensure_future(nemo_relay.tools.execute('task',{},tool))
                        try:
                            await asyncio.wait_for(started.wait(),5)
                            if not task_first:
                                release_worker.set();worker_tasks[0].cancel('client disconnected')
                                await asyncio.wait_for(worker_ended.wait(),5)
                                await asyncio.wait_for(worker_end_seen.wait(),5)
                                self.assertFalse(native.done())
                                self.assertFalse(task_end_seen.is_set())
                            native.cancel('client disconnected')
                            try: await native
                            except asyncio.CancelledError as error:
                                original_errors.append(error)
                                await asyncio.wait_for(task_end_seen.wait(),5)
                                await asyncio.wait_for(closing.wait(),5)
                                if task_first:
                                    self.assertFalse(worker_ended.is_set())
                                    self.assertFalse(any(event.get('scope_category')=='end' and
                                        event['name'] in ('researcher','application-request')
                                        for event,_ in recordings))
                                release_worker.set();release_tool.set()
                                await asyncio.wait_for(worker_ended.wait(),5)
                                await asyncio.gather(*worker_tasks,return_exceptions=True)
                                callback.on_chain_error(error,run_id=orchestrator)
                                raise
                            else: self.fail('Native managed tool lost cancellation')
                        finally:
                            release_worker.set();release_tool.set()
                            if not native.done(): native.cancel()
                            await asyncio.gather(native,*worker_tasks,return_exceptions=True)
                self.assertIs(caught.exception,original_errors[0])
                self.assertEqual(str(caught.exception),'client disconnected')
            finally:
                await nemo_relay.subscribers.flush_async()
                nemo_relay.subscribers.deregister(name)
        asyncio.run(execute())
        events=[event for event,_ in recordings]
        for event,native_timestamp in recordings:
            self.assertEqual(event['timestamp'],native_timestamp)
        start_events=[event for event in events if event.get('scope_category')=='start']
        starts={event['uuid']:event for event in start_events}
        self.assertEqual(len(start_events),len(starts))
        ends=[event for event in events if event.get('scope_category')=='end']
        self.assertEqual(set(starts),{event['uuid'] for event in ends})
        self.assertEqual(len(ends),len(starts))
        by_name={event['name']:event for event in ends}
        self.assertEqual(set(by_name),{'application-request','orchestrator','researcher','task'})
        root,orchestrator,worker,task=(by_name[name] for name in ('application-request','orchestrator','researcher','task'))
        self.assertEqual(task['parent_uuid'],worker['parent_uuid'])
        self.assertEqual(worker['parent_uuid'],orchestrator['uuid'])
        self.assertEqual(orchestrator['parent_uuid'],root['uuid'])
        for event in ends:
            self.assertEqual(event['metadata']['otel.status_code'],'ERROR')
            self.assertLessEqual(timestamp_ns(starts[event['uuid']]),timestamp_ns(event))
            self.assertLessEqual(timestamp_ns(starts[root['uuid']]),timestamp_ns(starts[event['uuid']]))
            self.assertLessEqual(timestamp_ns(event),timestamp_ns(root))
        for event in (worker,task):
            self.assertLessEqual(timestamp_ns(starts[orchestrator['uuid']]),timestamp_ns(starts[event['uuid']]))
            self.assertLessEqual(timestamp_ns(event),timestamp_ns(orchestrator))
        first,last=(task,worker) if task_first else (worker,task)
        self.assertLessEqual(timestamp_ns(first),timestamp_ns(last))
        # Equal wall-clock ticks remain possible. Publication order still proves
        # which real end callback happened first without rewriting timestamps.
        self.assertLess(events.index(first),events.index(last))

    def run_case(self, cancel, early_close=False, generator_exit=False,controlled_cleanup=False):
        path=Path(__file__).parents[1]/'relay_tracing.py'
        spec=importlib.util.spec_from_file_location('relay_cancellation_test',path)
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        recordings=[]; name='nested-cancel-'+uuid4().hex
        child=PausingModel(pause=cancel or early_close or generator_exit)
        if controlled_cleanup: child.cleanup_release=asyncio.Event()
        def record(event):
            data = event.to_dict()
            recordings.append((data, event.timestamp))
        nemo_relay.subscribers.register(name,record)
        async def execute():
            callback=getattr(runtime,'cancellation_callback',lambda cls:cls())(NemoRelayDeepAgentsCallbackHandler)
            manage=getattr(runtime,'managed_stream',baseline_stream)
            parent=ParentModel(responses=[AIMessage(content='',tool_calls=[{'id':'task-1','name':'task','args':{'subagent_type':'researcher','description':'Produce the answer.'}}]),AIMessage(content='finished')])
            graph=create_deep_agent(**add_nemo_relay_integration({'model':parent,'tools':[],
                'subagents':[{'name':'researcher','description':'Answer using the fake model.','model':child,'tools':[]}]}))
            seen=asyncio.Event()
            explicit_close=GeneratorExit('consumer closed')
            async def consume():
                with runtime.request_scope('cancel-test',input={'request':'answer'}) as request:
                    async with manage(graph.astream({'messages':[{'role':'user','content':'delegate'}]},
                        config={'callbacks':[callback]},stream_mode='messages',subgraphs=True),callback) as stream:
                        async for _,(message,_) in stream:
                            if isinstance(message,AIMessageChunk) and message.content=='visible partial':
                                seen.set()
                                if early_close: break
                                if generator_exit: raise explicit_close
                    request['output']='finished'
            task=asyncio.create_task(consume())
            try:
                await asyncio.wait_for(seen.wait(),5)
                if cancel:
                    task.cancel('client disconnected')
                    if controlled_cleanup:
                        await asyncio.wait_for(child.cleanup_entered.wait(),5)
                        self.assertFalse(task.done(),'A stopped graph does not acknowledge the actual model finalizer')
                        self.assertFalse(any(event.get('scope_category')=='end' and event['name'] in
                                             ('researcher','DeepAgent','application-request') for event,_ in recordings))
                        child.cleanup_release.set()
                    with self.assertRaises(asyncio.CancelledError) as caught: await task
                    self.assertEqual(str(caught.exception),'client disconnected')
                    if hasattr(callback,'pending_chains'):
                        self.assertFalse(callback.pending_chains)
                        callback.finish_cancelled(caught.exception)
                elif early_close or generator_exit:
                    with self.assertRaises(GeneratorExit) as caught: await task
                    if generator_exit: self.assertIs(caught.exception,explicit_close)
                    self.assertFalse(callback.pending_chains)
                else: await asyncio.wait_for(task,5)
            finally:
                if child.cleanup_release is not None: child.cleanup_release.set()
                if not task.done(): task.cancel(); await asyncio.gather(task,return_exceptions=True)
                await nemo_relay.subscribers.flush_async()
                # A loop shutdown would cancel unrelated live tasks. Observe
                # the real model finalizer while this loop is still running.
                await asyncio.wait_for(child.model_stopped.wait(),5)
                self.assertEqual([stage for stage,_ in child.lifecycle],
                                 ['partial','stopped'] if cancel or early_close or generator_exit else ['partial','finished','stopped'])
        try: asyncio.run(execute())
        finally: nemo_relay.subscribers.deregister(name)
        self.assertEqual([stage for stage,_ in child.lifecycle],
                         ['partial','stopped'] if cancel or early_close or generator_exit else ['partial','finished','stopped'])
        events = [event for event, _ in recordings]
        for event, native_timestamp in recordings:
            self.assertEqual(event['timestamp'], native_timestamp, 'Preserve the exact official SDK timestamp')
        start_events=[event for event in events if event.get('scope_category')=='start']
        starts={event['uuid']:event for event in start_events}
        self.assertEqual(len(start_events),len(starts))
        ends=[event for event in events if event.get('scope_category')=='end']
        self.assertTrue(any(event['name']=='researcher' for event in starts.values()))
        end_ids=[event['uuid'] for event in ends]
        self.assertEqual(len(end_ids),len(set(end_ids)))
        self.assertEqual(set(starts),set(end_ids))
        root=next(event for event in ends if event['name']=='application-request')
        self.assertLessEqual(child.lifecycle[-1][1],timestamp_ns(root),
                             'Request root must include the actual Python model finalizer')
        self.assertEqual(root['metadata']['otel.status_code'],'ERROR' if cancel or early_close or generator_exit else 'OK')
        worker_end=next(event for event in ends if event['name']=='researcher')
        task_end=next(event for event in events if event.get('scope_category')=='end' and event['name']=='task')
        orchestrator_end=next(event for event in ends if event['uuid']==worker_end['parent_uuid'])
        self.assertEqual(task_end['parent_uuid'], worker_end['parent_uuid'])
        self.assertEqual(orchestrator_end['parent_uuid'], root['uuid'])
        if cancel or early_close or generator_exit:
            self.assertEqual(worker_end['metadata']['otel.status_code'], 'ERROR')
            self.assertEqual(task_end['metadata']['otel.status_code'], 'ERROR')
        stamp=timestamp_ns
        self.assertLessEqual(stamp(worker_end),stamp(orchestrator_end),
                             'Orchestrator must include stopped child cleanup')
        self.assertLessEqual(stamp(orchestrator_end),stamp(root))
        for event in ends:
            self.assertLessEqual(stamp(starts[event['uuid']]),stamp(event))
            self.assertLessEqual(stamp(starts[root['uuid']]),stamp(starts[event['uuid']]))
            early_native_guard=(early_close or generator_exit) and event is task_end
            if early_native_guard:
                # NR002 is limited to the exact cancelled native task guard.
                # The helper joins admitted Python model tasks; every model,
                # business scope and actual consumer cancellation stays strict.
                self.assertEqual(event['metadata']['otel.status_code'],'ERROR')
                self.assertEqual(event['category'],'tool')
                self.assertEqual(event['metadata']['otel.status_description'],'tool execution cancelled')
                if stamp(event)>stamp(root):
                    print('Relay SDK NR002 early-closed native cancellation cleanup:',
                          {'closure':'GeneratorExit' if generator_exit else 'early-break',
                           'category':event['category'],'name':event['name'],
                           'root_end':root['timestamp'],'native_end':event['timestamp'],
                           'native_tail_ns':stamp(event)-stamp(root)})
            else:
                self.assertLessEqual(stamp(event),stamp(root),
                                     f"Request must include actual {event['category']} {event['name']} cleanup")
        if early_close or generator_exit:
            stopped_ns=child.lifecycle[-1][1]
            print('Relay SDK NR002 early-stop model finalizer observed:',
                  {'closure':'GeneratorExit' if generator_exit else 'early-break',
                   'root_end':root['timestamp'],'model_stopped_ns':stopped_ns,
                   'model_cleanup_tail_ns':stopped_ns-stamp(root),
                   'emitted':['visible partial'],'finalizer_completed':True})
        if not (cancel or early_close or generator_exit):
            self.assertLessEqual(stamp(worker_end),stamp(task_end),
                                 'Delegated worker must stop before its task execution ends')

class CleanupOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_cleanup_excludes_consumer_and_keeps_requests_separate(self):
        spec=importlib.util.spec_from_file_location('relay_model_isolation_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        first=runtime.cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
        second=runtime.cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
        first.on_llm_start({},['same-task model'],run_id=uuid4())
        await asyncio.wait_for(first.wait_model_cleanup(asyncio.current_task()),5)
        entered,release=asyncio.Event(),asyncio.Event()
        async def another_request():
            second.on_llm_start({},['other request model'],run_id=uuid4())
            entered.set();await release.wait()
        other=asyncio.create_task(another_request())
        try:
            await entered.wait()
            await asyncio.wait_for(first.wait_model_cleanup(asyncio.current_task()),5)
            self.assertFalse(other.done())
        finally:
            release.set();await other

    async def test_second_cancel_during_actual_model_join_preserves_original_error(self):
        spec=importlib.util.spec_from_file_location('relay_model_join_cancel_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        callback=runtime.cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
        entered,closing,release,graph_closed,consuming=(asyncio.Event() for _ in range(5))
        finalizers=[]
        async def actual_model_call():
            callback.on_llm_start({},['answer'],run_id=uuid4())
            entered.set()
            try: await asyncio.Event().wait()
            finally:
                closing.set();await release.wait();finalizers.append('stopped')
        class Stream:
            async def aclose(self): graph_closed.set()
        async def consume():
            async with runtime.managed_stream(Stream(),callback):
                consuming.set()
                await asyncio.Event().wait()
        model=asyncio.create_task(actual_model_call());await entered.wait()
        consumer=asyncio.create_task(consume())
        await consuming.wait()
        consumer.cancel('original consumer cancellation');model.cancel()
        try:
            await closing.wait();await graph_closed.wait()
            consumer.cancel('second cancellation')
            delivered=asyncio.Event()
            asyncio.get_running_loop().call_soon(delivered.set)
            await delivered.wait()
            self.assertFalse(consumer.done());self.assertEqual(finalizers,[])
            release.set()
            with self.assertRaises(asyncio.CancelledError) as caught:await consumer
            self.assertEqual(str(caught.exception),'original consumer cancellation')
            self.assertEqual(finalizers,['stopped'])
        finally:
            release.set();await asyncio.gather(model,consumer,return_exceptions=True)

    async def test_real_model_finalizer_defers_worker_and_orchestrator_errors(self):
        spec=importlib.util.spec_from_file_location('relay_live_model_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        entered,closing,release,stopped=(asyncio.Event() for _ in range(4))
        errors=[];events=[];name='live-model-'+uuid4().hex
        class HeldModel(PausingModel):
            async def _astream(self,messages,**kwargs):
                try:
                    entered.set()
                    yield ChatGenerationChunk(message=AIMessageChunk(content='visible partial'))
                    await asyncio.Event().wait()
                except asyncio.CancelledError as error:
                    errors.append(error);raise
                finally:
                    closing.set();await release.wait();stopped.set()
        callback=runtime.cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
        orchestrator,worker_run=uuid4(),uuid4()
        metadata={'lc_versions':{'deepagents':'0.7.21'},'lc_agent_name':'orchestrator'}
        nemo_relay.subscribers.register(name,lambda event:events.append(event.to_dict()))
        model_task=None
        try:
            with self.assertRaises(asyncio.CancelledError) as caught:
                with runtime.request_scope('live-model',input={'request':'cancel'}):
                    callback.on_chain_start({}, {},run_id=orchestrator,name='orchestrator',metadata=metadata)
                    callback.on_chain_start({}, {},run_id=worker_run,parent_run_id=orchestrator,
                        name='researcher',metadata={**metadata,'lc_agent_name':'researcher'})
                    manager=AsyncCallbackManager([callback],[callback],parent_run_id=worker_run)
                    async def model_call():
                        async for _ in HeldModel().astream('answer',config={'callbacks':manager}): pass
                    model_task=asyncio.create_task(model_call())
                    await asyncio.wait_for(entered.wait(),5)
                    model_task.cancel('client disconnected');await asyncio.wait_for(closing.wait(),5)
                    callback.on_chain_error(errors[0],run_id=worker_run)
                    callback.on_chain_error(errors[0],run_id=orchestrator)
                    await nemo_relay.subscribers.flush_async()
                    self.assertFalse(model_task.done())
                    self.assertFalse(stopped.is_set())
                    self.assertFalse(any(event.get('scope_category')=='end' for event in events),
                                     'Real live model cleanup must defer both business agent end callbacks')
                    release.set()
                    await asyncio.gather(model_task,return_exceptions=True)
                    self.assertTrue(stopped.is_set())
                    callback.finish_cancelled(errors[0]);raise errors[0]
            self.assertIs(caught.exception,errors[0])
            self.assertFalse(callback.pending_chains)
        finally:
            release.set()
            if model_task is not None: await asyncio.gather(model_task,return_exceptions=True)
            await nemo_relay.subscribers.flush_async();nemo_relay.subscribers.deregister(name)
        ends=[event for event in events if event.get('scope_category')=='end']
        self.assertEqual([event['name'] for event in ends],['researcher','orchestrator','application-request'])
        for event in ends:self.assertEqual(event['metadata']['otel.status_code'],'ERROR')
        self.assertLessEqual(timestamp_ns(ends[0]),timestamp_ns(ends[1]))
        self.assertLessEqual(timestamp_ns(ends[1]),timestamp_ns(ends[2]))

    async def test_second_cancel_waits_for_graph_close_and_keeps_original_error(self):
        spec=importlib.util.spec_from_file_location('relay_cleanup_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        entered,closing,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
        closed=[]; finished=[]
        class Stream:
            async def aclose(self):
                closing.set(); await release.wait(); closed.append(True)
        class Callback:
            def finish_cancelled(self,error):
                self_test.assertEqual(closed,[True]); finished.append(error)
        self_test=self
        async def consume():
            async with runtime.managed_stream(Stream(),Callback()):
                entered.set(); await asyncio.Event().wait()
        task=asyncio.create_task(consume());await entered.wait();task.cancel('original')
        await closing.wait();task.cancel('second');await asyncio.sleep(0)
        self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError) as caught: await task
        self.assertEqual(str(caught.exception),'original');self.assertEqual(finished,[caught.exception])

    async def test_consumer_failure_is_not_replaced_by_cancel_during_cleanup(self):
        spec=importlib.util.spec_from_file_location('relay_first_failure_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        closing,release=asyncio.Event(),asyncio.Event();finished=[]
        original=ValueError('consumer rejected output')
        class Stream:
            async def aclose(self): closing.set();await release.wait()
        class Callback:
            def finish_stopped(self,error): finished.append(error)
            def finish_cancelled(self,error): raise AssertionError('consumer failure was replaced')
        async def consume():
            async with runtime.managed_stream(Stream(),Callback()): raise original
        task=asyncio.create_task(consume());await closing.wait();task.cancel('late cancel')
        await asyncio.sleep(0);self.assertFalse(task.done());release.set()
        with self.assertRaises(ValueError) as caught: await task
        self.assertIs(caught.exception,original);self.assertEqual(finished,[original])

    async def test_telemetry_cleanup_failure_retains_cancel_and_explicit_cause(self):
        spec=importlib.util.spec_from_file_location('relay_cleanup_failure_test',Path(__file__).parents[1]/'relay_tracing.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        class Stream:
            async def aclose(self): pass
        class Callback:
            def finish_cancelled(self,error): raise RuntimeError('cleanup unavailable')
        failure=asyncio.CancelledError('request cancelled')
        with self.assertRaises(asyncio.CancelledError) as caught:
            async with runtime.managed_stream(Stream(),Callback()): raise failure
        self.assertIs(caught.exception,failure)
        self.assertIsInstance(caught.exception.__cause__,RuntimeError)
        self.assertIn('cleanup unavailable',str(caught.exception.__cause__))


if __name__=='__main__': unittest.main()
