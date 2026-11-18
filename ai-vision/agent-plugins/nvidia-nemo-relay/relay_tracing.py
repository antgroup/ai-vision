"""Copy as relay_tracing.py: one official Relay exporter per application process.

Python 3.11+ / nemo-relay 0.9.3. Call start_tracing at service startup and
stop_tracing (or await stop_tracing_async) after all requests finish at shutdown.
"""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import json
import os
from threading import Lock

import nemo_relay


_exporter = None
_exporter_lock = Lock()
_subscriber_name = "aivision-relay"
_request_receipts = ContextVar("aivision_relay_request_receipts", default=None)


class _RequestReceipts:
    """Acknowledge admitted native calls through this root's public subscriber."""
    def __init__(self):
        self.lock = Lock()
        self.started = set()
        self.ended = set()
        self.loop = None
        self.changed = None

    def record(self, event):
        if event.kind != "scope":
            return
        with self.lock:
            (self.started if event.scope_category == "start" else self.ended).add(event.uuid)
            loop, changed = self.loop, self.changed
        if loop is not None and changed is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(changed.set)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def has_ended(self, native_id):
        with self.lock:
            return native_id in self.started and native_id in self.ended

    async def wait(self, native_ids):
        with self.lock:
            self.loop = asyncio.get_running_loop()
            if self.changed is None:
                self.changed = asyncio.Event()
        while True:
            with self.lock:
                if native_ids <= self.started and native_ids <= self.ended:
                    return
                self.changed.clear()
            await self.changed.wait()


def start_tracing():
    global _exporter
    with _exporter_lock:
        if _exporter is not None:
            return _exporter
        conflicts = [
            key for key in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            if os.environ.get(key)
        ]
        if conflicts:
            raise RuntimeError(
                "Explicit Relay exporter conflicts with " + ", ".join(conflicts)
                + ". Choose one tracing exporter in this application's environment. "
                "This example does not change your environment."
            )
        endpoint = os.environ["AIVISION_OTLP_ENDPOINT"]
        if not os.environ.get("AIVISION_OTLP_AUTHORIZATION"):
            raise ValueError("Set AIVISION_OTLP_AUTHORIZATION from the AI Vision onboarding page")
        config = nemo_relay.OpenTelemetryConfig("gen_ai", endpoint)
        config.transport = "http_binary"
        config.service_name = os.environ.get("AIVISION_SERVICE_NAME", "relay-application")
        config.header_env = {"authorization": "AIVISION_OTLP_AUTHORIZATION"}
        config.promote_metadata_prefixes = ["aivision.", "langfuse.user.id"]
        exporter = nemo_relay.OpenTelemetrySubscriber(config)
        try:
            exporter.register(_subscriber_name)
        except BaseException:
            exporter.shutdown()
            raise
        _exporter = exporter
        return exporter


def stop_tracing():
    global _exporter
    with _exporter_lock:
        exporter, _exporter = _exporter, None
        if exporter is None:
            return
        try:
            # Relay event delivery and OTLP export have separate queues.
            nemo_relay.subscribers.flush()
            exporter.force_flush()
        finally:
            try:
                exporter.deregister(_subscriber_name)
            finally:
                exporter.shutdown()


async def stop_tracing_async():
    try:
        await nemo_relay.subscribers.flush_async()
    finally:
        # The native exporter lifecycle is synchronous; do not block the event loop.
        await asyncio.to_thread(stop_tracing)


@contextmanager
def request_scope(session_id, *, user_id=None, input=None):
    """Use inside each request/task; metadata is scoped, never a global injector.

    The yielded dict provides trace_id and accepts a JSON-compatible output.
    Reuse the business session/thread ID across turns, not across unrelated users.
    """
    metadata = {"gen_ai.conversation.id": str(session_id)}
    if user_id is not None:
        # GenAI 0.9.3 does not project business users; user./gen_ai. promotion
        # is reserved. AI Vision already accepts this application span alias.
        metadata["langfuse.user.id"] = str(user_id)
    # Relay 0.9.3's GenAI Agent projection omits scope input/output. Official
    # metadata promotion preserves application-owned JSON aliases; reserved
    # gen_ai./input./output. namespaces cannot be added through promotion.
    # Keep these aliases on the root lifecycle only, outside the child injector.
    root_metadata = {
        **metadata,
        "aivision.request.input": json.dumps(input, ensure_ascii=False, allow_nan=False),
        "aivision.request.input.encoding": "json",
    }
    # asyncio tasks inherit ContextVars, including a mutable Relay scope stack.
    # Each independent application request owns a new root and restores its caller.
    with nemo_relay.use_scope_stack(nemo_relay.create_scope_stack()):
        handle = nemo_relay.scope.push(
            "application-request", nemo_relay.ScopeType.Agent, input=input, metadata=root_metadata
        )
        result = {"trace_id": nemo_relay.capture_traceparent().split("-")[1], "output": None}
        status = {"otel.status_code": "OK"}
        receipts = _RequestReceipts()
        receipt_token = _request_receipts.set(receipts)
        try:
            # Register before native calls start so their managed lifecycle
            # snapshots include this request's receipt subscriber.
            nemo_relay.scope_local.register_subscriber(handle, "aivision-request-cleanup", receipts.record)
            nemo_relay.scope_local.register_event_metadata_injector(
                handle, "aivision-request", 0, lambda event: dict(metadata)
            )
            yield result
        except BaseException as exc:
            status = {"otel.status_code": "ERROR", "otel.status_description": type(exc).__name__}
            raise
        finally:
            try:
                status["aivision.request.output"] = json.dumps(
                    result["output"], ensure_ascii=False, allow_nan=False
                )
                status["aivision.request.output.encoding"] = "json"
                nemo_relay.scope.pop(handle, output=result["output"], metadata=status)
            except BaseException as failure:
                try:
                    nemo_relay.scope_local.deregister_subscriber(handle, "aivision-request-cleanup")
                except Exception as receipt_error:
                    failure.add_note(f"Request receipt cleanup failed: {type(receipt_error).__name__}: {receipt_error}")
                raise
            finally:
                _request_receipts.reset(receipt_token)


def cancellation_callback(handler_class):
    """Create one request-owned official handler with missing-chain cleanup.

    Pass NemoRelayCallbackHandler or NemoRelayDeepAgentsCallbackHandler. Relay
    middleware still owns tool/model execution. Only real chain starts missing
    their terminal callback are ended after graph closure and cleanup of this
    handler's actual async model tasks. Joining them is not an acknowledgement
    of every native SDK guard; see NR-002 in KNOWN_ISSUES.md.
    """
    class RequestCancellationHandler(handler_class):
        run_inline = True

        def __init__(self):
            super().__init__()
            self.pending_chains = {}
            self.run_parents = {}
            self.deferred_errors = {}
            self.model_tasks = {}
            self.native_calls = {}

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
            result = super().on_chain_start(serialized, inputs, run_id=run_id,
                                            parent_run_id=parent_run_id, **kwargs)
            self.pending_chains[run_id] = parent_run_id
            self.run_parents[run_id] = parent_run_id
            return result

        def on_chain_end(self, outputs, *, run_id, **kwargs):
            try:
                return super().on_chain_end(outputs, run_id=run_id, **kwargs)
            finally:
                self.pending_chains.pop(run_id, None)

        def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
            # A subagent chain's public parent can be the task tool run rather
            # than another chain. Keep that real edge for descendant checks.
            self.run_parents[run_id] = parent_run_id
            self._record_native_call(run_id)
            return super().on_tool_start(serialized, input_str, run_id=run_id,
                                         parent_run_id=parent_run_id, **kwargs)

        def _record_model_task(self, run_id, parent_run_id):
            try:
                task = asyncio.current_task()
            except RuntimeError:
                return
            if task is not None:
                # Inline LangChain callbacks run in the actual model callable,
                # including the Python task owned by Relay's native bridge.
                self.model_tasks[run_id] = task
                self.run_parents[run_id] = parent_run_id
                self._record_native_call(run_id)

        def _record_native_call(self, run_id):
            receipts = _request_receipts.get()
            if receipts is None:
                return
            native_id = nemo_relay.capture_propagation_context().parent_uuid
            handle = nemo_relay.scope.get_handle()
            # Without managed Relay middleware the callback's parent is the
            # current agent scope; waiting on that scope would join ourselves.
            if native_id is not None and (handle is None or native_id != handle.uuid):
                self.native_calls[run_id] = (receipts, native_id)

        def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
            self._record_model_task(run_id, parent_run_id)
            return super().on_chat_model_start(serialized, messages, run_id=run_id,
                                               parent_run_id=parent_run_id, **kwargs)

        def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
            self._record_model_task(run_id, parent_run_id)
            return super().on_llm_start(serialized, prompts, run_id=run_id,
                                       parent_run_id=parent_run_id, **kwargs)

        async def wait_model_cleanup(self, consumer_task, consumer_native_id=None):
            # Graph aclose can return before Relay's scheduled Python callable
            # handles cancellation. Join only this handler's admitted model
            # tasks, preserving their actual finalizer and event times.
            tasks = set(self.model_tasks.values()) - {consumer_task, asyncio.current_task()}
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            by_request = {}
            for receipts, native_id in self.native_calls.values():
                # A stream can itself run inside a native tool/LLM callable.
                # That enclosing call ends only after this consumer returns.
                if native_id != consumer_native_id:
                    by_request.setdefault(receipts, set()).add(native_id)
            for receipts, native_ids in by_request.items():
                await receipts.wait(native_ids)
            self.model_tasks.clear()
            self.native_calls.clear()

        def _has_pending_descendants(self, run_id):
            pending = dict(self.pending_chains)
            pending.update({model_id: self.run_parents.get(model_id)
                            for model_id, task in self.model_tasks.items() if not task.done()})
            pending.update({run_id: self.run_parents.get(run_id)
                            for run_id, (receipts, native_id) in self.native_calls.items()
                            if not receipts.has_ended(native_id)})
            for child_id, parent_id in pending.items():
                if child_id == run_id:
                    continue
                seen = set()
                while parent_id is not None and parent_id not in seen:
                    if parent_id == run_id:
                        return True
                    seen.add(parent_id)
                    parent_id = self.run_parents.get(parent_id)
            return False

        def on_chain_error(self, error, *, run_id, **kwargs):
            if self._has_pending_descendants(run_id):
                # Relay timestamps on_chain_error before its LIFO pop queue
                # drains. Defer this real callback until child cleanup ends;
                # do not backdate spans or end a still-running worker.
                self.deferred_errors[run_id] = (error, kwargs)
                return None
            try:
                return super().on_chain_error(error, run_id=run_id, **kwargs)
            finally:
                self.pending_chains.pop(run_id, None)
                self.deferred_errors.pop(run_id, None)

        def finish_stopped(self, error):
            if not isinstance(error, BaseException):
                raise TypeError("finish_stopped requires the stream's actual failure or closure")
            # The stream and this request's model tasks have closed. Finish actual
            # child starts first, then forward each ancestor's original error.
            for run_id, parent_run_id in reversed(list(self.pending_chains.items())):
                original, fields = self.deferred_errors.get(
                    run_id, (error, {"parent_run_id": parent_run_id})
                )
                self.on_chain_error(original, run_id=run_id, **fields)
            if not self.pending_chains:
                self.run_parents.clear()

        def finish_cancelled(self, error):
            if not isinstance(error, asyncio.CancelledError):
                raise TypeError("finish_cancelled requires the actual CancelledError")
            self.finish_stopped(error)

    return RequestCancellationHandler()


@asynccontextmanager
async def managed_stream(stream, callback):
    """Close the graph and owned model tasks before finishing chain scopes.

    Leaving the context before exhaustion raises GeneratorExit after cleanup.
    Normal exhaustion remains unchanged; callback timing is not used to guess it.
    Joins only this callback's model tasks, not other requests or all SDK guards.
    """
    class ObservedIterator:
        exhausted = False
        iterator = None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.iterator is None:
                self.iterator = stream.__aiter__()
            try:
                return await self.iterator.__anext__()
            except StopAsyncIteration:
                self.exhausted = True
                raise

        async def aclose(self):
            await stream.aclose()

    observed = ObservedIterator()
    consumer_task = asyncio.current_task()
    consumer_native_id = nemo_relay.capture_propagation_context().parent_uuid
    failure = None
    try:
        yield observed
    except BaseException as error:
        failure = error
    async def close_and_join():
        try:
            await stream.aclose()
        finally:
            if hasattr(callback, "wait_model_cleanup"):
                await callback.wait_model_cleanup(consumer_task, consumer_native_id)
    cleanup = asyncio.ensure_future(close_and_join())
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as error:
            # Preserve a consumer failure when cancellation arrives in cleanup.
            failure = failure or error
        except BaseException:
            break
    try:
        cleanup.result()
    except BaseException as close_error:
        if failure is not None:
            failure.add_note(f"Stream cleanup failed: {type(close_error).__name__}: {close_error}")
        else:
            failure = close_error
    if failure is None and not observed.exhausted:
        # Consumption stopped before the stream reported normal exhaustion.
        failure = GeneratorExit("Stream consumption ended before graph completion")
    if failure is not None:
        try:
            if isinstance(failure, asyncio.CancelledError):
                callback.finish_cancelled(failure)
            else:
                callback.finish_stopped(failure)
        except Exception as telemetry_error:
            failure.add_note(f"Stream telemetry cleanup failed: {type(telemetry_error).__name__}: {telemetry_error}")
            raise failure from telemetry_error
        raise failure
