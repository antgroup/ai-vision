"""Offline behavior checks for the exact onboarding assets served by the catalog.

Run with the documented, pinned framework dependencies; no model or platform calls.
"""

import asyncio
import importlib.util
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import yaml


RELAY = Path(__file__).resolve().parents[1]
PLUGINS = RELAY.parent


def load_runtime():
    spec = importlib.util.spec_from_file_location("relay_tracing", RELAY / "relay_tracing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guide(name):
    return yaml.safe_load((RELAY / "examples" / f"{name}.yaml").read_text())


class OnboardingAssetsTests(unittest.TestCase):
    def test_relay_catalog_only_contains_official_sdk_examples(self):
        examples = [yaml.safe_load(p.read_text()) for p in (RELAY / "examples").glob("*.yaml")]
        examples = [example for example in examples if example.get("language") != "typescript"]
        self.assertEqual({e["id"] for e in examples}, {"langchain-agent-middleware", "langgraph", "deep-agents"})
        for example in examples:
            self.assertIn(">=0.9.3,<0.10", example["pipInstall"])
            self.assertNotIn("nemoguardrails", example["pipInstall"])
            self.assertNotIn("openinference", example["pipExample"])
            self.assertEqual(example["bootstrapFile"], "relay_tracing.py")
            self.assertIn("existing_graph" if example["id"] == "langgraph" else "existing_agent_kwargs", example["existingExample"])
            compile(example["pipExample"], example["id"], "exec")

    def test_langgraph_existing_application_preserves_graph_and_business_config(self):
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableConfig
        from langgraph.graph import END, START, MessagesState, StateGraph

        received = []

        def increment(state: MessagesState, config: RunnableConfig):
            received.append(config)
            return {"messages": [AIMessage(content=str(int(state["messages"][-1].content) + 1))]}

        builder = StateGraph(MessagesState)
        builder.add_node("increment", increment)
        builder.add_edge(START, "increment")
        builder.add_edge("increment", END)
        graph = builder.compile()
        business_callback = BaseCallbackHandler()
        config = {"callbacks": [business_callback], "configurable": {"thread_id": "business-thread"},
                  "metadata": {"business": "retained"}, "tags": ["existing-tag"], "recursion_limit": 17}
        runtime = load_runtime()
        events = []
        runtime.nemo_relay.subscribers.register("langgraph-existing-test", lambda event: events.append(event.to_dict()))
        namespace = {"existing_graph": graph, "existing_config": config,
                     "existing_input": {"messages": [{"role": "user", "content": "41"}]}, "session_id": "business-thread"}
        try:
            with patch.dict(sys.modules, {"relay_tracing": runtime}), patch.object(
                runtime, "start_tracing"
            ), patch.object(runtime, "stop_tracing"):
                exec(compile(guide("langgraph")["existingExample"], "langgraph-existing", "exec"), namespace)
            runtime.nemo_relay.subscribers.flush()
        finally:
            runtime.nemo_relay.subscribers.deregister("langgraph-existing-test")
        self.assertEqual([message.content for message in namespace["result"]["messages"]], ["41", "42"])
        self.assertEqual(namespace["request"]["output"], "42")
        self.assertEqual(received[0]["configurable"]["thread_id"], "business-thread")
        self.assertEqual(received[0]["metadata"]["business"], "retained")
        self.assertIn("existing-tag", received[0]["tags"])
        self.assertEqual(received[0]["recursion_limit"], 17)
        self.assertIn(business_callback, received[0]["callbacks"].handlers)
        self.assertEqual(config["callbacks"], [business_callback])
        starts = {event["name"]: event for event in events if event.get("scope_category") == "start"}
        self.assertEqual(starts["LangGraph"]["parent_uuid"], starts["application-request"]["uuid"])
        self.assertEqual(starts["increment"]["parent_uuid"], starts["LangGraph"]["uuid"])

    def test_langgraph_complete_example_traces_nested_agent_and_tool(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage

        class ToolModel(FakeMessagesListChatModel):
            model_name: str = "onboarding-test-model"

            def bind_tools(self, tools, **kwargs):
                return self

        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "multiply", "args": {"a": 7, "b": 9}, "id": "multiply-1"}]),
            AIMessage(content="63"),
        ])
        runtime = load_runtime()
        events = []
        runtime.nemo_relay.subscribers.register("langgraph-complete-test", lambda event: events.append(event.to_dict()))
        namespace = {"__name__": "onboarding_example", "os": os,
                     "start_tracing": lambda: None, "stop_tracing": lambda: None,
                     "request_scope": runtime.request_scope}
        env = {"OPENAI_API_KEY": "example-only", "OPENAI_MODEL": "test-model", "OPENAI_BASE_URL": "https://model.example/v1"}
        try:
            exec(compile(guide("langgraph")["pipExample"], "langgraph-complete", "exec"), namespace)
            with patch.dict(os.environ, env), patch.dict(namespace, {"ChatOpenAI": lambda **kwargs: model}):
                namespace["main"]()
            runtime.nemo_relay.subscribers.flush()
        finally:
            runtime.nemo_relay.subscribers.deregister("langgraph-complete-test")
        starts = [event for event in events if event.get("scope_category") == "start"]
        self.assertTrue(any(event["name"] == "assistant" for event in starts))
        self.assertTrue(any(event.get("category") == "llm" for event in starts))
        tool_ends = [event for event in events if event.get("scope_category") == "end" and event["name"] == "multiply"]
        self.assertTrue(tool_ends)
        self.assertIn("63", json.dumps(tool_ends))
        roots = [event for event in events if event.get("scope_category") == "end" and event["name"] == "application-request"]
        self.assertEqual(json.loads(roots[0]["metadata"]["aivision.request.output"]), "63")

    def test_langfuse_deepagents_declares_its_actual_model_credentials(self):
        data = yaml.safe_load((PLUGINS / "langfuse/examples/langchain-deepagents.yaml").read_text())
        for field in ("envShell", "envDotenv"):
            env = "\n".join(data[field])
            self.assertIn("OPENAI_API_KEY=", env)
            self.assertIn("OPENAI_MODEL=", env)
            self.assertIn("OPENAI_BASE_URL=", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("TAVILY_API_KEY", env)

    def test_langfuse_deepagents_flushes_when_business_call_fails(self):
        path = PLUGINS / "langfuse/examples/langchain-deepagents.yaml"
        data = yaml.safe_load(path.read_text())
        namespace = {"__name__": "onboarding_example", "__file__": str(path)}
        env = {
            "OPENAI_API_KEY": "example-only",
            "OPENAI_MODEL": "developer-model",
            "OPENAI_BASE_URL": "https://model.example/v1",
            "LANGFUSE_PUBLIC_KEY": "example-app",
            "LANGFUSE_SECRET_KEY": "example-key",
            "LANGFUSE_BASE_URL": "http://localhost:8001",
        }
        client = MagicMock()
        with patch.dict(os.environ, env, clear=True), patch("langfuse.get_client", return_value=client):
            exec(compile(data["pipExample"], "langfuse-deepagents", "exec"), namespace)
            model = MagicMock()
            agent = MagicMock()
            agent.invoke.side_effect = RuntimeError("business failure")
            with patch.dict(namespace, {"ChatOpenAI": model, "create_deep_agent": MagicMock(return_value=agent)}):
                with self.assertRaisesRegex(RuntimeError, "business failure"):
                    namespace["main"]()
            self.assertEqual(model.call_args.kwargs["model"], "developer-model")
            self.assertEqual(model.call_args.kwargs["base_url"], "https://model.example/v1")
        client.flush.assert_called_once()
        client.shutdown.assert_called_once()

    def test_existing_application_keeps_business_kwargs_and_callbacks(self):
        from langchain_core.callbacks import BaseCallbackHandler
        from nemo_relay.integrations.langchain import NemoRelayMiddleware
        from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsMiddleware

        for name, factory_path, relay_type in (
            ("langchain-agent-middleware", "langchain.agents.create_agent", NemoRelayMiddleware),
            ("deep-agents", "deepagents.create_deep_agent", NemoRelayDeepAgentsMiddleware),
        ):
            with self.subTest(framework=name):
                runtime = load_runtime()
                model, backend, checkpointer, business_middleware = (object() for _ in range(4))
                business_callback = BaseCallbackHandler()
                subagent = {"name": "worker", "description": "Example worker", "model": model,
                            "tools": ["existing-tool"], "middleware": [business_middleware]}
                kwargs = {"model": model, "tools": ["existing-tool"], "middleware": [business_middleware],
                          "checkpointer": checkpointer, "system_prompt": "existing policy", "name": "existing-agent"}
                if name == "deep-agents":
                    kwargs.update(backend=backend, subagents=[subagent])
                config = {"callbacks": [business_callback], "configurable": {"thread_id": "business-thread"},
                          "metadata": {"business": "value"}, "tags": ["business-tag"], "recursion_limit": 17}
                inputs = {"messages": [{"role": "user", "content": "existing question"}]}
                namespace = {"existing_agent_kwargs": kwargs, "existing_config": config,
                             "existing_input": inputs, "session_id": "business-thread"}
                agent = MagicMock()
                agent.invoke.return_value = {"messages": [SimpleNamespace(content="business result")]}
                with patch.dict(sys.modules, {"relay_tracing": runtime}), patch(factory_path, return_value=agent) as factory, patch.object(
                    runtime, "start_tracing"
                ), patch.object(runtime, "stop_tracing"):
                    exec(compile(guide(name)["existingExample"], name, "exec"), namespace)
                created = factory.call_args.kwargs
                for key in ("model", "tools", "checkpointer", "system_prompt", "name"):
                    self.assertIs(created[key], kwargs[key])
                self.assertIs(created["middleware"][0], business_middleware)
                self.assertEqual(sum(isinstance(item, relay_type) for item in created["middleware"]), 1)
                self.assertEqual(kwargs["middleware"], [business_middleware])
                if name == "deep-agents":
                    self.assertIs(created["backend"], backend)
                    child = created["subagents"][0]
                    self.assertIs(child["model"], model)
                    self.assertIs(child["tools"], subagent["tools"])
                    self.assertEqual(sum(isinstance(item, relay_type) for item in child["middleware"]), 1)
                    self.assertEqual(subagent["middleware"], [business_middleware])
                self.assertIs(agent.invoke.call_args.args[0], inputs)
                forwarded = agent.invoke.call_args.kwargs["config"]
                self.assertIn(business_callback, forwarded["callbacks"])
                self.assertEqual(forwarded["configurable"], config["configurable"])
                self.assertEqual(forwarded["recursion_limit"], 17)
                self.assertEqual(config["callbacks"], [business_callback])


class RelayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.env = {
            "AIVISION_OTLP_ENDPOINT": "http://localhost:8001/api/public/otel/v1/traces",
            "AIVISION_OTLP_AUTHORIZATION": "Basic example-only",
        }

    def test_repeated_start_registers_once_and_preserves_environment(self):
        exporter = MagicMock()
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.runtime.nemo_relay, "OpenTelemetrySubscriber", return_value=exporter
        ) as factory:
            before = dict(os.environ)
            self.assertIs(self.runtime.start_tracing(), self.runtime.start_tracing())
            self.assertEqual(dict(os.environ), before)
            factory.assert_called_once()
            exporter.register.assert_called_once()
            config = factory.call_args.args[0]
            self.assertEqual(config.transport, "http_binary")
            self.assertEqual(config.header_env, {"authorization": "AIVISION_OTLP_AUTHORIZATION"})
            self.runtime.stop_tracing()
            exporter.shutdown.assert_called_once()

    def test_automatic_otel_configuration_is_rejected_without_mutation(self):
        for variable in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            with self.subTest(variable=variable), patch.dict(
                os.environ, {**self.env, variable: "https://existing.example"}, clear=True
            ):
                before = dict(os.environ)
                with self.assertRaisesRegex(RuntimeError, variable):
                    self.runtime.start_tracing()
                self.assertEqual(dict(os.environ), before)

    def test_shutdown_releases_exporter_even_when_flush_raises(self):
        exporter = MagicMock()
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.runtime.nemo_relay, "OpenTelemetrySubscriber", return_value=exporter
        ), patch.object(self.runtime.nemo_relay.subscribers, "flush", side_effect=RuntimeError("flush failed")):
            self.runtime.start_tracing()
            with self.assertRaisesRegex(RuntimeError, "flush failed"):
                self.runtime.stop_tracing()
            exporter.deregister.assert_called_once()
            exporter.shutdown.assert_called_once()

    def test_async_shutdown_flushes_both_queues_and_releases_exporter(self):
        exporter = MagicMock()
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            self.runtime.nemo_relay, "OpenTelemetrySubscriber", return_value=exporter
        ):
            self.runtime.start_tracing()
            asyncio.run(self.runtime.stop_tracing_async())
            exporter.force_flush.assert_called_once()
            exporter.shutdown.assert_called_once()

    def test_async_requests_keep_scope_metadata_separate(self):
        events = []
        relay = self.runtime.nemo_relay
        relay.subscribers.register("onboarding-test-events", lambda event: events.append(event.to_dict()))

        async def request(session_id):
            with self.runtime.request_scope(session_id, input={"session": session_id}) as request:
                self.assertEqual(len(request["trace_id"]), 32)
                await asyncio.sleep(0)
                relay.scope.event(session_id)

        async def run():
            await asyncio.gather(request("session-a"), request("session-b"))
            await relay.subscribers.flush_async()

        try:
            # A startup/parent scope already populated the inherited ContextVar.
            with relay.scope.scope("service-parent", relay.ScopeType.Agent):
                asyncio.run(run())
                relay.scope.event("parent-still-active")
            relay.subscribers.flush()
        finally:
            relay.subscribers.deregister("onboarding-test-events")
        marks = {event.get("name"): event for event in events if event.get("name") in {"session-a", "session-b"}}
        self.assertEqual(set(marks), {"session-a", "session-b"})
        for name, event in marks.items():
            self.assertEqual(event["metadata"]["gen_ai.conversation.id"], name)

    def test_official_exporter_keeps_business_users_scoped_across_concurrent_requests(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        captured = []
        class Collector(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                if self.headers.get('Content-Encoding') == 'gzip': body = gzip.decompress(body)
                captured.append(ExportTraceServiceRequest.FromString(body))
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-protobuf')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
        worker = Thread(target=server.serve_forever, daemon=True); worker.start()
        relay = self.runtime.nemo_relay
        users = {'session-alpha': 'business-user-alpha', 'session-beta': 'business-user-beta',
                 'session-anonymous': None}
        async def request(sid, user_id):
            with self.runtime.request_scope(sid, user_id=user_id, input={'request': sid}):
                await asyncio.sleep(0)
                with relay.scope.scope('child-' + sid, relay.ScopeType.Function):
                    await asyncio.sleep(0)
        async def run():
            with relay.scope.scope('outside-request', relay.ScopeType.Agent):
                await asyncio.gather(*(request(sid, user) for sid, user in users.items()))
            await self.runtime.stop_tracing_async()
        try:
            with patch.dict(os.environ, {**self.env, 'AIVISION_OTLP_ENDPOINT':
                    f'http://127.0.0.1:{server.server_port}/v1/traces'}, clear=True):
                self.runtime.start_tracing()
                try: asyncio.run(run())
                finally: self.runtime.stop_tracing()
        finally:
            server.shutdown(); server.server_close(); worker.join()
        spans = [(span, {a.key: a.value.string_value for a in span.attributes})
                 for payload in captured for resource in payload.resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        roots = {attrs['gen_ai.conversation.id']: span for span, attrs in spans
                 if 'gen_ai.conversation.id' in attrs and not span.parent_span_id}
        self.assertEqual(set(roots), set(users))
        self.assertEqual(len({span.trace_id for span in roots.values()}), len(users))
        for sid, user in users.items():
            own = [(span, attrs) for span, attrs in spans if span.trace_id == roots[sid].trace_id]
            self.assertEqual(len(own), 2, 'Each request must export its root and child')
            for span, attrs in own:
                if user is None: self.assertNotIn('langfuse.user.id', attrs)
                else: self.assertEqual(attrs.get('langfuse.user.id'), user)
        outside = [attrs for span, attrs in spans if span.name == 'invoke_agent outside-request']
        self.assertEqual(len(outside), 1)
        self.assertNotIn('langfuse.user.id', outside[0])
        # Business user is a span alias, never an authentication resource claim.
        for payload in captured:
            for resource in payload.resource_spans:
                self.assertNotIn('user.id', {a.key for a in resource.resource.attributes})
                self.assertNotIn('ant.user.id', {a.key for a in resource.resource.attributes})
                self.assertNotIn('langfuse.user.id', {a.key for a in resource.resource.attributes})

    def test_official_exporter_preserves_root_business_io_without_copying_to_children(self):
        """Removing root metadata loses IO in Relay 0.9.3's GenAI projection."""
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        captured = []
        events = []
        self.runtime.nemo_relay.subscribers.register(
            "root-io-encoding-test", lambda event: events.append(event.to_dict())
        )

        class Collector(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                if self.headers.get('Content-Encoding') == 'gzip':
                    data = gzip.decompress(data)
                captured.append(ExportTraceServiceRequest.FromString(data))
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-protobuf')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        cases = [({'question': 'business input', 'zero': 0}, {'answer': 'business output', 'empty': []}),
                 (0, False), ('raw string input', 'raw string output'), (None, None),
                 ('42', 'null'), ('true', 'false'), ('[{broken', '{\"role\":\"assistant\"}')]
        try:
            with patch.dict(os.environ, {**self.env, 'AIVISION_OTLP_ENDPOINT':
                    f'http://127.0.0.1:{server.server_port}/v1/traces'}, clear=True):
                self.runtime.start_tracing()
                try:
                    for index, (business_input, business_output) in enumerate(cases):
                        with self.runtime.request_scope(f'root-io-{index}', input=business_input) as request:
                            with self.runtime.nemo_relay.scope.scope('child-with-own-io', self.runtime.nemo_relay.ScopeType.Function):
                                pass
                            request['output'] = business_output
                finally:
                    self.runtime.stop_tracing()
        finally:
            server.shutdown(); server.server_close(); worker.join()
            self.runtime.nemo_relay.subscribers.deregister("root-io-encoding-test")
        spans = [span for payload in captured for resource in payload.resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        roots = {next(a.value.string_value for a in span.attributes if a.key == 'gen_ai.conversation.id'): span
                 for span in spans if not span.parent_span_id}
        self.assertEqual(len(roots), len(cases))
        for index, (business_input, business_output) in enumerate(cases):
            attrs = {a.key: a.value.string_value for a in roots[f'root-io-{index}'].attributes}
            self.assertIn('aivision.request.input', attrs)
            self.assertIn('aivision.request.output', attrs)
            self.assertEqual(attrs['aivision.request.input.encoding'], 'json')
            self.assertEqual(attrs['aivision.request.output.encoding'], 'json')
            self.assertEqual(json.loads(attrs['aivision.request.input']), business_input)
            self.assertEqual(json.loads(attrs['aivision.request.output']), business_output)
            self.assertIs(type(json.loads(attrs['aivision.request.input'])), type(business_input))
            self.assertIs(type(json.loads(attrs['aivision.request.output'])), type(business_output))
        starts = [event for event in events if event.get('scope_category') == 'start'
                  and event['name'] == 'application-request']
        ends = [event for event in events if event.get('scope_category') == 'end'
                and event['name'] == 'application-request']
        self.assertEqual(len(starts), len(cases))
        self.assertEqual(len(ends), len(cases))
        for event in starts:
            self.assertEqual(event['metadata']['aivision.request.input.encoding'], 'json')
            self.assertNotIn('aivision.request.output.encoding', event['metadata'])
        for event in ends:
            self.assertEqual(event['metadata']['aivision.request.output.encoding'], 'json')
        children = [span for span in spans if span.name == 'child-with-own-io']
        self.assertEqual(len(children), len(cases))
        for child in children:
            self.assertFalse({'aivision.request.input', 'aivision.request.output',
                              'aivision.request.input.encoding', 'aivision.request.output.encoding'}
                             & {a.key for a in child.attributes})


if __name__ == "__main__":
    unittest.main()
