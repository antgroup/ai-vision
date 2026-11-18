"""Execute published onboarding snippets against real local framework objects."""

from contextlib import redirect_stdout
import io
import httpx
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TypedDict
import unittest
from unittest.mock import patch
import uuid

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langfuse import Langfuse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import yaml


class OnboardingExampleTests(unittest.TestCase):
    def test_opted_in_inline_media_is_exported_without_attachment_upload(self):
        import base64
        from langfuse.langchain import CallbackHandler
        from langchain_core.messages import HumanMessage
        from langchain_core.runnables import RunnableLambda
        image = "data:image/png;base64," + base64.b64encode(b"test-image").decode()
        key = "inline-media-test-" + uuid.uuid4().hex
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource({}))
        environment = {"LANGFUSE_PUBLIC_KEY": key, "LANGFUSE_SECRET_KEY": "test-secret",
                       "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false"}
        with patch.dict(os.environ, environment), \
                patch("requests.sessions.Session.send", side_effect=AssertionError("unexpected network")), \
                patch("httpx.Client.send", side_effect=AssertionError("unexpected network")), \
                patch.object(OTLPSpanExporter, "export", side_effect=exporter.export):
            client = Langfuse(public_key=key, secret_key="test-secret", base_url="http://127.0.0.1:9",
                              tracer_provider=provider)
            try:
                app = RunnableLambda(lambda message: message.content)
                app.invoke(HumanMessage(content=[{"type": "text", "text": "inspect"},
                                                  {"type": "image_url", "image_url": {"url": image}}]),
                           config={"callbacks": [CallbackHandler(public_key=key)]})
                client.flush()
                spans = exporter.get_finished_spans()
                serialized = json.dumps([dict(span.attributes) for span in spans])
                self.assertIn(image, serialized)
                self.assertNotIn("@@@langfuseMedia:", serialized)
            finally:
                client.shutdown()

    def test_openai_example_exports_usage_and_shuts_down_on_success_and_error(self):
        mode = os.environ.get("AIVISION_ONBOARDING_OPENAI_MODE")
        if mode is None:
            for mode in ("success", "error"):
                with self.subTest(mode=mode):
                    environment = {key: value for key, value in os.environ.items()
                                   if not key.startswith(("LANGFUSE_", "OTEL_", "OPENAI_"))}
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()),
                         "OnboardingExampleTests.test_openai_example_exports_usage_and_shuts_down_on_success_and_error"],
                        env={**environment, "AIVISION_ONBOARDING_OPENAI_MODE": mode},
                        text=True, capture_output=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        from langfuse.openai import openai
        calls = []
        def provider_response(request):
            calls.append(request)
            self.assertEqual(request.url.host, "local-provider.invalid")
            self.assertEqual(request.url.path, "/v1/chat/completions")
            if mode == "error":
                return httpx.Response(400, json={"error": {"message": "local model error", "type": "invalid_request_error"}})
            return httpx.Response(200, json={"id": "chat-local", "object": "chat.completion", "created": 1,
                "model": "gpt-4o", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "2"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource({}))
        key = "onboarding-test-" + uuid.uuid4().hex
        path = Path(__file__).resolve().parents[1] / "examples/openai.yaml"
        snippet = yaml.safe_load(path.read_text())["pipExample"]
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": key, "LANGFUSE_SECRET_KEY": "local-test-only",
                                    "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false", "OPENAI_API_KEY": "local-test-only",
                                    "OPENAI_BASE_URL": "https://local-provider.invalid/v1"}), \
                patch("requests.sessions.Session.send", side_effect=AssertionError("network forbidden")), \
                patch.object(OTLPSpanExporter, "export", side_effect=exporter.export):
            client = Langfuse(public_key=key, secret_key="local-test-only", base_url="http://127.0.0.1:9",
                              tracer_provider=provider)
            openai.http_client = httpx.Client(transport=httpx.MockTransport(provider_response))
            try:
                with patch.object(Langfuse, "shutdown", autospec=True, side_effect=Langfuse.shutdown) as shutdown:
                    with redirect_stdout(io.StringIO()):
                        if mode == "error":
                            with self.assertRaises(openai.BadRequestError):
                                exec(compile(snippet, str(path), "exec"), {"__name__": "__main__"})
                        else:
                            exec(compile(snippet, str(path), "exec"), {"__name__": "__main__"})
                    self.assertEqual(len(calls), 1)
                    shutdown.assert_called_once()
                generations = [span for span in exporter.get_finished_spans()
                               if span.attributes.get("langfuse.observation.type") == "generation"]
                self.assertEqual(len(generations), 1)
                if mode == "success":
                    usage = json.loads(generations[0].attributes["langfuse.observation.usage_details"])
                    self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]), (3, 1, 4))
                else:
                    self.assertEqual(generations[0].attributes["langfuse.observation.level"], "ERROR")
            finally:
                client.shutdown()
                provider.shutdown()
                openai.http_client.close()

    def test_existing_langchain_and_deepagents_preserve_application(self):
        selected = os.environ.get("AIVISION_ONBOARDING_FRAMEWORK")
        if selected is None:
            for framework in ("langchain", "langchain-deepagents"):
                with self.subTest(framework=framework):
                    environment = {key: value for key, value in os.environ.items()
                                   if not key.startswith(("LANGFUSE_", "OTEL_", "LANGCHAIN_", "LANGSMITH_"))}
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()),
                         "OnboardingExampleTests.test_existing_langchain_and_deepagents_preserve_application"],
                        env={**environment, "AIVISION_ONBOARDING_FRAMEWORK": framework},
                        text=True, capture_output=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return

        class LocalModel(BaseChatModel):
            @property
            def _llm_type(self):
                return "local-onboarding-contract"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, **kwargs):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="observed-answer"))])

        class BusinessCallback(BaseCallbackHandler):
            runs = 0
            def on_chain_start(self, serialized, inputs, **kwargs):
                self.runs += 1

        if selected == "langchain":
            from langchain.agents import create_agent
            agent = create_agent(model=LocalModel(), tools=[], checkpointer=InMemorySaver())
        else:
            from deepagents import create_deep_agent
            agent = create_deep_agent(model=LocalModel(), tools=[], checkpointer=InMemorySaver())
        business = BusinessCallback()
        config = {"callbacks": [business], "configurable": {"thread_id": "published-example"}}
        inputs = {"messages": [{"role": "user", "content": "local input"}]}
        namespace = {"existing_agent": agent, "existing_config": config,
                     "existing_input": inputs, "session_id": "published-session"}
        path = Path(__file__).resolve().parents[1] / "examples" / (selected + ".yaml")
        snippet = yaml.safe_load(path.read_text())["existingExample"]
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource({}))
        key = "onboarding-test-" + uuid.uuid4().hex
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": key, "LANGFUSE_SECRET_KEY": "local-test-only",
                                    "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false"}), \
                patch("requests.sessions.Session.send", side_effect=AssertionError("network forbidden")), \
                patch("httpx.Client.send", side_effect=AssertionError("network forbidden")), \
                patch.object(OTLPSpanExporter, "export", side_effect=exporter.export):
            client = Langfuse(public_key=key, secret_key="local-test-only", base_url="http://127.0.0.1:9",
                              tracer_provider=provider)
            try:
                with redirect_stdout(io.StringIO()):
                    exec(compile(snippet, str(path), "exec"), namespace)
                client.flush()
                self.assertEqual(namespace["result"]["messages"][-1].content, "observed-answer")
                self.assertEqual(agent.get_state(config).values["messages"][-1].content, "observed-answer")
                self.assertGreater(business.runs, 0)
                self.assertEqual(config["callbacks"], [business])
                spans = exporter.get_finished_spans()
                roots = [span for span in spans if span.name == ("langchain" if selected == "langchain" else "deepagents") + "-request"]
                self.assertEqual(len(roots), 1)
                self.assertEqual(roots[0].attributes["langfuse.observation.output"], "observed-answer")
                self.assertTrue(any(span.attributes.get("langfuse.observation.type") == "generation" for span in spans))
            finally:
                client.shutdown()
                provider.shutdown()

    def test_existing_langgraph_preserves_custom_state_result(self):
        # Langfuse caches clients and processors process-wide. Keep this snippet's
        # default-client lookup isolated from the other SDK integration tests.
        if os.environ.get("AIVISION_ONBOARDING_TEST_CHILD") != "1":
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("LANGFUSE_", "OTEL_", "LANGCHAIN_", "LANGSMITH_"))}
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "OnboardingExampleTests.test_existing_langgraph_preserves_custom_state_result"],
                env={**environment, "AIVISION_ONBOARDING_TEST_CHILD": "1"},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            return

        class State(TypedDict):
            value: int

        class BusinessCallback(BaseCallbackHandler):
            def __init__(self):
                self.runs = 0

            def on_chain_start(self, serialized, inputs, **kwargs):
                self.runs += 1

        callback = BusinessCallback()
        graph = (
            StateGraph(State)
            .add_node("increment", lambda state: {"value": state["value"] + 1})
            .add_edge(START, "increment")
            .add_edge("increment", END)
            .compile(checkpointer=InMemorySaver())
        )
        config = {"configurable": {"thread_id": "custom-state"}, "callbacks": [callback]}
        namespace = {
            "existing_graph": graph,
            "existing_input": {"value": 1},
            "existing_config": config,
            "session_id": "custom-state-session",
        }
        path = Path(__file__).resolve().parents[1] / "examples/langgraph.yaml"
        snippet = yaml.safe_load(path.read_text())["existingExample"]
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource({}))
        key = "onboarding-test-" + uuid.uuid4().hex
        network_attempts = []

        def reject_http(*_args, **_kwargs):
            network_attempts.append(True)
            raise AssertionError("The onboarding regression must only use its memory exporter")

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": key,
            "LANGFUSE_SECRET_KEY": "local-test-only",
            "LANGFUSE_TRACING_ENABLED": "true",
            "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false",
        }), patch("requests.sessions.Session.send", side_effect=reject_http), \
                patch("httpx.Client.send", side_effect=reject_http), \
                patch.object(OTLPSpanExporter, "export", side_effect=exporter.export):
            client = Langfuse(public_key=key, secret_key="local-test-only",
                              base_url="http://127.0.0.1:9", tracer_provider=provider)
            try:
                with redirect_stdout(io.StringIO()):
                    exec(compile(snippet, str(path), "exec"), namespace)
                client.flush()

                self.assertEqual(namespace["result"], {"value": 2})
                self.assertEqual(graph.get_state(config).values, {"value": 2})
                self.assertGreater(callback.runs, 0)
                roots = [span for span in exporter.get_finished_spans()
                         if span.name == "langgraph-request"]
                self.assertEqual(len(roots), 1)
                self.assertEqual(json.loads(roots[0].attributes["langfuse.observation.output"]),
                                 {"value": 2})
            finally:
                client.shutdown()
                provider.shutdown()
            self.assertEqual(network_attempts, [], "The memory-only test attempted an HTTP request")


if __name__ == "__main__":
    unittest.main()
