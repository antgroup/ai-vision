"""Real LangChain cancellation and real Langfuse OTLP; no model/network service."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
import uuid

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


class PausingModel(BaseChatModel):
    model_name: str = "cancellation-contract"
    pause: bool = True
    structured: bool = False

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "cancellation-contract"

    def _generate(self, *args, **kwargs):
        raise AssertionError("The stream must use the asynchronous model")

    async def _astream(self, *args, **kwargs):
        content = [{"type": "text", "text": "visible partial"}] if self.structured else "visible partial"
        yield ChatGenerationChunk(message=AIMessageChunk(content=content))
        if self.pause:
            await asyncio.Event().wait()
        yield ChatGenerationChunk(message=AIMessageChunk(content=" finished", usage_metadata={
            "input_tokens": 3, "output_tokens": 2, "total_tokens": 5,
        }))


class DelegatingModel(PausingModel):
    async def _astream(self, messages, *args, **kwargs):
        last_tool = next((message for message in reversed(messages) if isinstance(message, ToolMessage)), None)
        if last_tool is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content=str(last_tool.content)))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[{
                "name": "task", "id": "research-call", "index": 0,
                "args": json.dumps({"subagent_type": "researcher", "description": "Stream the requested answer."}),
            }]))


@asynccontextmanager
async def baseline_stream(stream, callback):
    try:
        yield stream
    finally:
        await stream.aclose()


class StreamingLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Tests own a loopback collector. Ambient proxies/tracing configuration
        # must not send SDK traffic to a workstation service or external host.
        excluded = ('LANGFUSE_', 'OTEL_', 'LANGCHAIN_', 'LANGSMITH_')
        proxy_keys = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'}
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(excluded) and key not in proxy_keys}
        environment.update(NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
        isolated = patch.dict(os.environ, environment, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)

    def test_real_rag_cancellation_ends_children_before_all_ancestors(self):
        self.run_rag_case("cancel")

    def test_real_rag_completed_stream_retains_usage_and_parent_intervals(self):
        self.run_rag_case("stream")

    def run_rag_case(self, scenario):
        repo = Path(__file__).resolve().parents[4]
        runner = repo / "ai-vision/agent-plugins/tests/framework_e2e/execute_rag.py"
        with tempfile.TemporaryDirectory(prefix="lf-rag-lifecycle-") as directory:
            output = Path(directory) / "capture"
            result = subprocess.run(
                [sys.executable, str(runner), "--framework", "deepagents", "--sdk", "langfuse",
                 "--scenario", scenario, "--collector-only", "--out", str(output), "--repo", str(repo)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            spans = [span for export in json.loads((output / "sdk-exports.json").read_text())
                     for span in export["spans"]]
        by_id = {span["span_id"]: span for span in spans}
        self.assertEqual(len(by_id), len(spans), "A run must not be exported twice")
        self.assertTrue({"researcher", "task", "rag-core", "retrieve_policy", "calculate_storage"}
                        .issubset({span["name"] for span in spans}))
        for span in spans:
            if not span["parent_span_id"]:
                continue
            self.assertIn(span["parent_span_id"], by_id, span["name"])
            parent = by_id[span["parent_span_id"]]
            self.assertLessEqual(int(parent["start_time_unix_nano"]), int(span["start_time_unix_nano"]),
                                 f"{parent['name']} must start before {span['name']}")
            self.assertLessEqual(int(span["end_time_unix_nano"]), int(parent["end_time_unix_nano"]),
                                 f"{span['name']} must end before {parent['name']}")
        generations = [span for span in spans
                       if span["attributes"].get("langfuse.observation.type") == "generation"]
        self.assertEqual(len(generations), 4 if scenario == "cancel" else 5)
        unfinished = [span for span in generations
                      if span["attributes"].get("langfuse.observation.level") == "ERROR"]
        self.assertEqual(len(unfinished), 1 if scenario == "cancel" else 0)
        if unfinished:
            attrs = unfinished[0]["attributes"]
            self.assertNotIn("langfuse.observation.usage_details", attrs)
            partial = json.loads(attrs["langfuse.observation.output"])
            self.assertFalse(partial["complete"])
            self.assertTrue(partial["partial_text"])
        for span in generations:
            if span not in unfinished:
                self.assertEqual(json.loads(span["attributes"]["langfuse.observation.usage_details"]),
                                 {"input": 13, "output": 5, "total": 18})

    def test_cancelled_agent_exports_partial_generation_and_propagates_cancellation(self):
        self.run_case()

    def test_deepagents_cancellation_keeps_partial_generation(self):
        self.run_case(framework="deepagents")

    def test_cancelled_deepagents_child_exports_task_and_complete_parent_graph(self):
        self.run_case(framework="deepagents", delegated=True)

    def test_cancelled_agent_keeps_partial_when_token_executor_is_busy(self):
        self.run_case(hold_token_executor=True)

    def test_cancelled_deepagents_child_keeps_partial_when_token_executor_is_busy(self):
        self.run_case(framework="deepagents", delegated=True, hold_token_executor=True)

    def test_successful_deepagents_child_preserves_normal_parent_graph(self):
        self.run_case(framework="deepagents", delegated=True, cancel=False)

    def test_early_break_marks_unfinished_deepagents_stream_as_error(self):
        self.run_case(framework="deepagents", delegated=True, cancel=False, abort="break")

    def test_consumer_error_keeps_original_exception_and_closes_nested_runs(self):
        self.run_case(framework="deepagents", delegated=True, cancel=False, abort="consumer-error")

    def test_generator_exit_keeps_original_exception_and_closes_nested_runs(self):
        self.run_case(framework="deepagents", delegated=True, cancel=False, abort="generator-exit")

    def test_success_keeps_complete_output_usage_and_single_generation(self):
        self.run_case(cancel=False)

    def test_structured_content_keeps_actual_text_and_cancellation(self):
        self.run_case(structured=True)

    def test_failed_partial_update_still_ends_generation_and_propagates_cancellation(self):
        self.run_case(update_failure=True)

    def run_case(self, framework="langchain", cancel=True, structured=False, update_failure=False, delegated=False, abort=None,
                 hold_token_executor=False):
        payloads = []
        incomplete = cancel or abort is not None
        original_error = (ValueError("consumer failed") if abort == "consumer-error" else
                          GeneratorExit("consumer closed") if abort == "generator-exit" else None)

        class Collector(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                payloads.append(ExportTraceServiceRequest.FromString(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        key = "test-" + uuid.uuid4().hex
        client = Langfuse(public_key=key, secret_key="local-test-only",
                          base_url=f"http://127.0.0.1:{server.server_port}",
                          flush_interval=0.1)
        if os.environ.get("STREAMING_BASELINE"):
            handler_type, managed_stream = CallbackHandler, baseline_stream
        else:
            spec = importlib.util.spec_from_file_location("streaming_lifecycle", Path(__file__).parents[1] / "streaming_lifecycle.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            handler_type, managed_stream = module.CancellationCallbackHandler, module.managed_stream

        async def scenario():
            seen = asyncio.Event()
            callback = handler_type(public_key=key)
            loop = asyncio.get_running_loop()
            run_in_executor = loop.run_in_executor
            held_tokens = []

            def controlled_executor(executor, function, *args):
                # LangChain wraps synchronous callbacks in copy_context().run.
                # Hold only this request's token callback, reproducing a busy
                # worker deterministically while the real graph emits messages.
                parameters = getattr(function, "args", ())
                event = parameters[0] if parameters else None
                if (getattr(event, "__self__", None) is callback
                        and getattr(event, "__name__", None) == "on_llm_new_token"
                        and len(parameters) > 1 and parameters[1] == "visible partial"):
                    pending = loop.create_future()
                    held_tokens.append(pending)
                    return pending
                return run_in_executor(executor, function, *args)
            if framework == "deepagents":
                from deepagents import create_deep_agent
                if delegated:
                    agent = create_deep_agent(model=DelegatingModel(), tools=[], subagents=[{
                        "name": "researcher", "description": "Stream an answer.",
                        "system_prompt": "Respond to the request.",
                        "model": PausingModel(pause=incomplete, structured=structured), "tools": [],
                    }])
                else:
                    agent = create_deep_agent(model=PausingModel(pause=cancel, structured=structured), tools=[])
            else:
                agent = create_agent(model=PausingModel(pause=cancel, structured=structured), tools=[])

            async def consume():
                with propagate_attributes(session_id="cancel-session"):
                    with client.start_as_current_observation(name="request", as_type="agent"):
                        stream = agent.astream({"messages": [{"role": "user", "content": "stream"}]},
                                               config={"callbacks": [callback]}, stream_mode="messages", version="v1",
                                               subgraphs=delegated)
                        async with managed_stream(stream, callback) as messages:
                            async for event in messages:
                                chunk, _metadata = event[1] if delegated else event
                                if chunk.content:
                                    seen.set()
                                    if abort == "break":
                                        break
                                    if original_error is not None:
                                        raise original_error
                                    if cancel:
                                        await asyncio.Event().wait()

            executor_context = (patch.object(loop, "run_in_executor", controlled_executor)
                                if hold_token_executor else nullcontext())
            with executor_context:
                task = asyncio.create_task(consume())
                await asyncio.wait_for(seen.wait(), timeout=5)
                if abort:
                    with self.assertRaises(type(original_error) if original_error is not None else RuntimeError) as caught:
                        await asyncio.wait_for(task, timeout=5)
                    if original_error is not None:
                        self.assertIs(caught.exception, original_error)
                    else:
                        self.assertIn("before completion", str(caught.exception))
                    self.assertFalse(callback.pending_models)
                    self.assertFalse(callback.pending_runs)
                elif cancel:
                    if update_failure:
                        with patch.object(callback.observation_client, "update_current_generation", side_effect=RuntimeError("telemetry unavailable")):
                            task.cancel("client disconnected")
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                    else:
                        task.cancel("client disconnected")
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    if not os.environ.get("STREAMING_BASELINE"):
                        self.assertFalse(callback.pending_models)
                        self.assertFalse(callback.pending_runs)
                        # A second finalization must not add a duplicate generation.
                        callback.finish_cancelled(asyncio.CancelledError())
                else:
                    await asyncio.wait_for(task, timeout=5)
                    self.assertFalse(callback.pending_models)
                    self.assertFalse(callback.pending_runs)
            for pending in held_tokens:
                pending.cancel()

        try:
            asyncio.run(scenario())
            client.flush()
        finally:
            client.shutdown()
            server.shutdown()
            server.server_close()
            worker.join()
        spans = [span for payload in payloads for resource in payload.resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        if hold_token_executor:
            by_id = {span.span_id: span for span in spans}
            self.assertEqual(len(by_id), len(spans), "Each observation must be exported once")
            for span in spans:
                if not span.parent_span_id:
                    continue
                self.assertIn(span.parent_span_id, by_id, span.name)
                parent = by_id[span.parent_span_id]
                self.assertLessEqual(parent.start_time_unix_nano, span.start_time_unix_nano,
                                     f"{parent.name} must start before {span.name}")
                self.assertLessEqual(span.end_time_unix_nano, parent.end_time_unix_nano,
                                     f"{span.name} must end before {parent.name}")
        generations = [span for span in spans if any(a.key == "langfuse.observation.type" and a.value.string_value == "generation" for a in span.attributes)]
        self.assertEqual(len(generations), (2 if incomplete else 3) if delegated else 1,
                         "Each started provider call must have one completed generation")
        if delegated:
            ids = {span.span_id for span in spans}
            self.assertEqual(len(ids), len(spans), "Normal completion and cancellation must not export duplicates")
            missing = [(span.name, span.parent_span_id.hex()) for span in spans
                       if span.parent_span_id and span.parent_span_id not in ids]
            self.assertEqual(missing, [], "Every nested observation must retain its exported parent")
            tasks = [span for span in spans if span.name == "task"]
            self.assertEqual(len(tasks), 1)
            task_attrs = {a.key: a.value.string_value for a in tasks[0].attributes}
            if incomplete:
                self.assertEqual(task_attrs.get("langfuse.observation.level"), "ERROR")
            else:
                self.assertNotEqual(task_attrs.get("langfuse.observation.level"), "ERROR")
            generation = next(span for span in generations if any(
                a.key == "langfuse.observation.output" and "visible partial" in a.value.string_value
                for a in span.attributes))
            self.assertLessEqual(generation.end_time_unix_nano, tasks[0].end_time_unix_nano)
        else:
            generation = generations[0]
        attrs = {a.key: a.value.string_value for a in generation.attributes}
        if not update_failure:
            self.assertIn("visible partial", attrs.get("langfuse.observation.output", ""))
        if incomplete:
            self.assertEqual(attrs.get("langfuse.observation.level"), "ERROR")
            self.assertNotIn("langfuse.observation.usage_details", attrs)
            if not update_failure:
                self.assertEqual(json.loads(attrs["langfuse.observation.output"]),
                                 {"partial_text": "visible partial", "complete": False})
        else:
            self.assertNotEqual(attrs.get("langfuse.observation.level"), "ERROR")
            self.assertIn("finished", attrs["langfuse.observation.output"])
            usage = json.loads(attrs["langfuse.observation.usage_details"])
            self.assertEqual(usage, {"input": 3, "output": 2, "total": 5})
        self.assertEqual(attrs.get("session.id"), "cancel-session")
        if abort:
            roots = [span for span in spans if span.name == "request"]
            self.assertEqual(len(roots), 1)
            self.assertEqual(roots[0].status.code, 2, "Aborted request root must be ERROR")


class ManagedCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        spec = importlib.util.spec_from_file_location("streaming_lifecycle", Path(__file__).parents[1] / "streaming_lifecycle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.managed_stream = module.managed_stream

    async def run_cleanup(self, *, close_failure=False, cancel_twice=False):
        entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        completed, finalized = [], []

        class Stream:
            async def aclose(self):
                closing.set()
                if cancel_twice:
                    await release.wait()
                if close_failure:
                    raise RuntimeError("close failed")
                completed.append(True)

        class Callback:
            def finish_cancelled(self, error):
                finalized.append(error)

        async def consume():
            async with self.managed_stream(Stream(), Callback()):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel("original disconnect")
        await closing.wait()
        if cancel_twice:
            task.cancel("second disconnect")
            await asyncio.sleep(0)
            self.assertFalse(task.done(), "Request must wait for actual stream cleanup")
            release.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        self.assertEqual(str(caught.exception), "original disconnect")
        self.assertEqual(finalized, [caught.exception])
        if close_failure:
            self.assertTrue(any("close failed" in note for note in caught.exception.__notes__))
        else:
            self.assertEqual(completed, [True])

    async def test_cleanup_failure_cannot_replace_original_cancellation(self):
        await self.run_cleanup(close_failure=True)

    async def test_second_cancellation_waits_for_close_and_finalizes_once(self):
        await self.run_cleanup(cancel_twice=True)


if __name__ == "__main__":
    unittest.main()
