"""Request-owned cancellation lifecycle for LangChain / Deep Agents streams.

Uses LangChain callbacks and public Langfuse / OpenTelemetry APIs only. Create one
handler for each request, then wrap its graph stream with ``managed_stream``.
Validated with langchain-core 1.6.6 and Langfuse 3.15.0 / 4.16.0 / 5.0.2.
"""
import asyncio
from contextlib import asynccontextmanager
import logging

from langfuse import get_client
from langfuse.langchain import CallbackHandler
from opentelemetry import trace


logger = logging.getLogger(__name__)


class StreamClosedEarlyError(RuntimeError):
    """The consumer closed a stream with unfinished observations."""


def _text_parts(content):
    """Keep actual text blocks; never stringify images, reasoning, or tool data."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            block if isinstance(block, str) else block["text"]
            for block in content
            if isinstance(block, str) or (
                isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        ]
    return []


class CancellationCallbackHandler(CallbackHandler):
    """Complete only request runs left open after an actual stream abort."""

    # LangGraph publishes message chunks through an inline callback. Record our
    # request state in the same dispatch before cancellation can overtake a
    # token queued in LangChain's executor; no callback work may outlive cleanup.
    run_inline = True

    def __init__(self, *, public_key=None, **kwargs):
        super().__init__(public_key=public_key, **kwargs)
        self.observation_client = get_client(public_key=public_key)
        self.pending_models = {}
        self.pending_runs = {}
        self.run_parents = {}
        self.deferred_errors = {}

    def _remember_run(self, run_id, parent_run_id, kind):
        # Starts precede child starts. Insertion order lets finalization close
        # children before their still-open ancestors without SDK internals.
        self.pending_runs[run_id] = {"parent_run_id": parent_run_id, "kind": kind}
        self.run_parents[run_id] = parent_run_id

    def _defer_ancestor_error(self, error, run_id, kwargs):
        for child_id, pending in self.pending_runs.items():
            if child_id == run_id:
                continue
            parent_id = pending["parent_run_id"]
            seen = set()
            while parent_id is not None and parent_id not in seen:
                if parent_id == run_id:
                    # LangGraph can report an ancestor failure before its model
                    # stream closes. Keep the real callback until aclose finishes
                    # and its children have ended; never adjust SDK timestamps.
                    self.deferred_errors.setdefault(run_id, (error, kwargs))
                    return True
                seen.add(parent_id)
                parent_id = self.run_parents.get(parent_id)
        return False

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        super().on_chain_start(serialized, inputs, run_id=run_id,
                               parent_run_id=parent_run_id, **kwargs)
        self._remember_run(run_id, parent_run_id, "chain")

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        try:
            return super().on_chain_end(outputs, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)

    def on_chain_error(self, error, *, run_id, **kwargs):
        if self._defer_ancestor_error(error, run_id, kwargs):
            return None
        try:
            return super().on_chain_error(error, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)
            self.deferred_errors.pop(run_id, None)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        super().on_tool_start(serialized, input_str, run_id=run_id,
                              parent_run_id=parent_run_id, **kwargs)
        self._remember_run(run_id, parent_run_id, "tool")

    def on_tool_end(self, output, *, run_id, **kwargs):
        try:
            return super().on_tool_end(output, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)

    def on_tool_error(self, error, *, run_id, **kwargs):
        if self._defer_ancestor_error(error, run_id, kwargs):
            return None
        try:
            return super().on_tool_error(error, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)
            self.deferred_errors.pop(run_id, None)

    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
        super().on_retriever_start(serialized, query, run_id=run_id,
                                   parent_run_id=parent_run_id, **kwargs)
        self._remember_run(run_id, parent_run_id, "retriever")

    def on_retriever_end(self, documents, *, run_id, **kwargs):
        try:
            return super().on_retriever_end(documents, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)

    def on_retriever_error(self, error, *, run_id, **kwargs):
        if self._defer_ancestor_error(error, run_id, kwargs):
            return None
        try:
            return super().on_retriever_error(error, run_id=run_id, **kwargs)
        finally:
            self.pending_runs.pop(run_id, None)
            self.deferred_errors.pop(run_id, None)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        super().on_chat_model_start(serialized, messages, run_id=run_id,
                                    parent_run_id=parent_run_id, **kwargs)
        self.pending_models[run_id] = {
            "parent_run_id": parent_run_id,
            "span": trace.get_current_span(),
            "chunks": [],
        }
        self._remember_run(run_id, parent_run_id, "llm")

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        pending = self.pending_models.get(run_id)
        if pending is not None and token:
            pending["chunks"].extend(_text_parts(token))
        return super().on_llm_new_token(token, run_id=run_id, **kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        try:
            return super().on_llm_end(response, run_id=run_id, **kwargs)
        finally:
            self.pending_models.pop(run_id, None)
            self.pending_runs.pop(run_id, None)

    def on_llm_error(self, error, *, run_id, **kwargs):
        pending = self.pending_models.get(run_id)
        if pending is not None:
            # The callback may run after ancestor scopes were closed. Restore the
            # exact recorded generation using the public OpenTelemetry context API.
            try:
                with trace.use_span(pending["span"], end_on_exit=False):
                    self.observation_client.update_current_generation(
                        output={"partial_text": "".join(pending["chunks"]), "complete": False},
                        metadata={"stream.cancelled": isinstance(error, asyncio.CancelledError),
                                  "stream.usage_complete": False},
                    )
            except Exception as update_error:
                # Updating output is best effort; closing the generation and
                # propagating the application's original failure are mandatory.
                logger.warning("Could not record partial generation output (%s)", type(update_error).__name__)
        try:
            return super().on_llm_error(error, run_id=run_id, **kwargs)
        finally:
            self.pending_models.pop(run_id, None)
            self.pending_runs.pop(run_id, None)

    def finish_cancelled(self, error):
        """Compatibility entrypoint for an actual cancellation."""
        if not isinstance(error, asyncio.CancelledError):
            raise TypeError("finish_cancelled requires the actual CancelledError")
        self.finish_aborted(error)

    def finish_aborted(self, error):
        """Call only after this request's graph stream has closed and stopped work."""
        if not isinstance(error, BaseException):
            raise TypeError("finish_aborted requires the actual stream failure")
        for run_id, pending in reversed(list(self.pending_runs.items())):
            finish = getattr(self, f"on_{pending['kind']}_error")
            original, fields = self.deferred_errors.get(
                run_id, (error, {"parent_run_id": pending["parent_run_id"]})
            )
            finish(original, run_id=run_id, **fields)
        if not self.pending_runs:
            self.run_parents.clear()


@asynccontextmanager
async def managed_stream(stream, callback):
    """Close the graph before ending unfinished observations; preserve failures.

    SDK flush/shutdown belongs after all request tasks have finished. This context
    manager never infers final usage. An early break with unfinished observations
    raises StreamClosedEarlyError rather than marking the request successful.
    """
    request_span = trace.get_current_span()
    failure = None
    try:
        yield stream
    except BaseException as error:
        failure = error

    # The request may be cancelled again while the graph is closing. Give close
    # its own task and wait for it before ending telemetry, without losing the
    # original cancellation object or leaving detached cleanup work behind.
    cleanup = asyncio.ensure_future(stream.aclose())
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as error:
            # Preserve the first failure even if cancellation arrives while
            # closing a stream whose consumer already raised another error.
            failure = failure or error
        except BaseException:
            break  # Retrieve the exact cleanup exception below.
    try:
        cleanup.result()
    except BaseException as close_error:
        if failure is not None:
            failure.add_note(f"Stream cleanup failed: {type(close_error).__name__}: {close_error}")
        else:
            failure = close_error
    if failure is None and (getattr(callback, "pending_runs", None)
                            or getattr(callback, "pending_models", None)):
        failure = StreamClosedEarlyError("Stream closed before completion with unfinished observations")
    if failure is not None:
        # OTel context managers normally record Exception, but GeneratorExit
        # (and cancellation) are BaseException. Mark the actual owning request
        # through its public span API; its context manager still owns ending it.
        if request_span.is_recording():
            try:
                request_span.set_status(trace.Status(trace.StatusCode.ERROR,
                                                    f"{type(failure).__name__}: {failure}"))
            except Exception as telemetry_error:
                failure.add_note(f"Request failure status update failed: {type(telemetry_error).__name__}")
        try:
            if isinstance(failure, asyncio.CancelledError):
                callback.finish_cancelled(failure)
            else:
                callback.finish_aborted(failure)
        except Exception as telemetry_error:
            failure.add_note(f"Stream telemetry cleanup failed: {type(telemetry_error).__name__}")
            logger.warning("Could not finalize stream telemetry (%s)", type(telemetry_error).__name__)
        raise failure
