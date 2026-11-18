from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import observ_hermes
from observ_hermes.config import AntIdentity, ObservHermesConfig
from observ_hermes.hooks.llm import (
    api_request_error,
    post_api_request,
    post_llm_call,
    pre_api_request,
    pre_llm_call,
)
from observ_hermes.hooks.session import (
    on_session_end,
    on_session_finalize,
    on_session_reset,
    on_session_start,
)
from observ_hermes.hooks.subagent import on_subagent_start, on_subagent_stop
from observ_hermes.hooks.tool import post_tool_call, pre_tool_call
from observ_hermes.pipeline import get_pipeline

ADAPTERS = {
    "on_session_start": on_session_start,
    "on_session_end": on_session_end,
    "on_session_finalize": on_session_finalize,
    "on_session_reset": on_session_reset,
    "pre_llm_call": pre_llm_call,
    "post_llm_call": post_llm_call,
    "pre_api_request": pre_api_request,
    "post_api_request": post_api_request,
    "api_request_error": api_request_error,
    "pre_tool_call": pre_tool_call,
    "post_tool_call": post_tool_call,
    "subagent_start": on_subagent_start,
    "subagent_stop": on_subagent_stop,
}


def fixture_hooks() -> dict[str, dict]:
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/hooks/v0.18.2/observer_envelopes.json"
        ).read_text()
    )
    return {entry["hook"]: entry["envelope"] for entry in fixture["hooks"]}


class HookAdapterTests(unittest.TestCase):
    def tearDown(self) -> None:
        observ_hermes._shutdown()

    def test_all_current_hooks_have_thin_adapters(self) -> None:
        self.assertEqual(set(ADAPTERS), set(fixture_hooks()))

    def test_disabled_production_registers_no_hooks_or_telemetry_runtime(self) -> None:
        context = SimpleNamespace(hooks={})
        context.register_hook = lambda name, callback: context.hooks.__setitem__(
            name, callback
        )
        disabled = ObservHermesConfig(enable_telemetry=False)
        with (
            patch.object(
                observ_hermes.ObservHermesConfig, "from_env", return_value=disabled
            ),
            patch.object(observ_hermes, "init_telemetry") as initialize,
        ):
            observ_hermes.register(context)
        initialize.assert_not_called()
        self.assertEqual(context.hooks, {})

    def test_official_execution_middleware_preserves_response_and_reported_usage(self) -> None:
        config = ObservHermesConfig(enable_telemetry=True)
        runtime = SimpleNamespace(config=config, force_flush=lambda: None, shutdown=lambda: None)
        context = SimpleNamespace(hooks={}, middleware={})
        context.register_hook = lambda name, callback: context.hooks.__setitem__(name, callback)
        context.register_middleware = lambda name, callback: context.middleware.__setitem__(name, callback)
        with (
            patch.object(observ_hermes.ObservHermesConfig, "from_env", return_value=config),
            patch.object(observ_hermes, "init_telemetry", return_value=runtime),
        ):
            observ_hermes.register(context)
        self.assertIn("llm_execution", context.middleware)
        response = SimpleNamespace(usage={"prompt_tokens": 7})
        calls = []
        def provider(request):
            calls.append(request)
            return response
        common = {"session_id": "native", "turn_id": "turn", "api_request_id": "request"}
        context.hooks["pre_api_request"](**common, model="model", provider="nvidia")
        observed = context.middleware["llm_execution"](
            **common, request={"model": "model"}, next_call=provider,
        )
        self.assertIs(observed, response)
        self.assertEqual(calls, [{"model": "model"}])
        context.hooks["post_api_request"](
            **common, usage={"input_tokens": 7, "output_tokens": 0},
            response={"usage": {"input_tokens": 7, "output_tokens": 0}},
        )
        sink = get_pipeline(runtime).memory_sink
        assert sink is not None
        end = next(item for item in sink.of_type("span.end") if item.key == "api:native:request")
        self.assertEqual(end.attributes["gen_ai.usage.input_tokens"], 7)
        self.assertEqual(end.attributes.get("agentic.usage.output_tokens.missing"), "true")

    def test_auxiliary_registration_follows_host_surface(self) -> None:
        config = ObservHermesConfig(enable_telemetry=True)
        runtime = SimpleNamespace(config=config, force_flush=lambda: None, shutdown=lambda: None)
        context = SimpleNamespace(hooks={})
        context.register_hook = lambda name, callback: context.hooks.__setitem__(name, callback)
        host = SimpleNamespace(VALID_HOOKS=set(fixture_hooks()) | {"pre_auxiliary_call", "post_auxiliary_call"})
        with (
            patch.dict(sys.modules, {"hermes_cli.plugins": host}),
            patch.object(observ_hermes.ObservHermesConfig, "from_env", return_value=config),
            patch.object(observ_hermes, "init_telemetry", return_value=runtime),
        ):
            observ_hermes.register(context)
        self.assertIn("pre_auxiliary_call", context.hooks)
        self.assertIn("post_auxiliary_call", context.hooks)

    def test_real_callback_sequence_reduces_without_production_persistence(
        self,
    ) -> None:
        captured = fixture_hooks()
        order = [
            "on_session_start",
            "pre_llm_call",
            "pre_api_request",
            "post_api_request",
            "pre_tool_call",
            "post_tool_call",
            "subagent_start",
            "subagent_stop",
            "post_llm_call",
            "on_session_end",
            "on_session_finalize",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            config = ObservHermesConfig(
                enable_telemetry=True,
                ant=AntIdentity(username="tester", uid="user-1"),
            )
            runtime = SimpleNamespace(config=config)
            for hook in order:
                ADAPTERS[hook](runtime, **captured[hook])
            pipeline = get_pipeline(runtime)
            self.assertIsNotNone(pipeline.memory_sink)
            assert pipeline.memory_sink is not None
            observations = list(pipeline.memory_sink.observations)
            pipeline.close()

            self.assertEqual(list(Path(tmp).glob("session_*.jsonl")), [])
            self.assertTrue(
                any(
                    item.type == "span.start" and item.span_kind == "AGENT"
                    for item in observations
                )
            )
            self.assertFalse(any("compaction" in item.name for item in observations))

    def test_registration_matches_v0182_surface_and_isolates_secret_bearing_failures(
        self,
    ) -> None:
        config = ObservHermesConfig(enable_telemetry=True)

        class FakeRuntime:
            def __init__(self) -> None:
                self.config = config
                self._observ_hermes_pipeline: Any = None

            def force_flush(self) -> None:
                pass

            def shutdown(self) -> None:
                pass

        class Context:
            def __init__(self) -> None:
                self.hooks = {}

            def register_hook(self, name, callback) -> None:
                self.hooks[name] = callback

        runtime = FakeRuntime()
        context = Context()
        with (
            patch.object(
                observ_hermes.ObservHermesConfig, "from_env", return_value=config
            ),
            patch.object(observ_hermes, "init_telemetry", return_value=runtime),
        ):
            observ_hermes.register(context)
        self.assertEqual(set(context.hooks), set(fixture_hooks()))

        class RaisingPipeline:
            def process(self, hook, payload) -> None:
                raise RuntimeError(f"do not log payload {payload}")

            def close(self) -> None:
                pass

        runtime._observ_hermes_pipeline = RaisingPipeline()
        secret = "Bearer should-never-appear"
        with self.assertLogs("observ_hermes", level="WARNING") as captured_logs:
            result = context.hooks["pre_api_request"](
                session_id="session-1",
                telemetry_schema_version="hermes.observer.v1",
                authorization=secret,
            )
        self.assertIsNone(result)
        rendered = "\n".join(captured_logs.output)
        self.assertIn("hook=pre_api_request", rendered)
        self.assertIn("error_type=RuntimeError", rendered)
        self.assertIn("failure_count=1", rendered)
        self.assertNotIn(secret, rendered)
        self.assertEqual(
            getattr(runtime, "_observ_hermes_hook_failures"),
            {"pre_api_request:RuntimeError": 1},
        )

    def test_close_discards_pending_state_without_unpersisted_terminal_telemetry(
        self,
    ) -> None:
        captured = fixture_hooks()
        with tempfile.TemporaryDirectory() as tmp:
            config = ObservHermesConfig(
                enable_telemetry=True,
                ant=AntIdentity(username="tester", uid="user-1"),
            )
            runtime = SimpleNamespace(config=config)
            pipeline = get_pipeline(runtime)
            pipeline.process("on_session_start", captured["on_session_start"])
            pipeline.process("pre_llm_call", captured["pre_llm_call"])
            assert pipeline.memory_sink is not None
            observations_before_close = list(pipeline.memory_sink.observations)

            pipeline.close()

            self.assertEqual(
                pipeline.memory_sink.observations, observations_before_close
            )
            self.assertEqual(
                pipeline.engine.state.pending_counts(),
                {
                    "sessions": 0,
                    "turns": 0,
                    "api_requests": 0,
                    "tools": 0,
                    "subagents": 0,
                },
            )
            self.assertEqual(list(Path(tmp).glob("session_*.jsonl")), [])

    def test_shutdown_failure_is_diagnostic_without_leaking_exception_content(
        self,
    ) -> None:
        secret = "Bearer shutdown-secret"

        class FailingRuntime:
            def force_flush(self) -> None:
                raise RuntimeError(secret)

            def shutdown(self) -> None:
                raise AssertionError("shutdown must not run after flush failure")

        observ_hermes._runtime = FailingRuntime()  # type: ignore[assignment]
        with self.assertLogs("observ_hermes", level="WARNING") as captured_logs:
            observ_hermes._shutdown()

        rendered = "\n".join(captured_logs.output)
        self.assertIn("shutdown failed", rendered)
        self.assertIn("error_type=RuntimeError", rendered)
        self.assertNotIn(secret, rendered)
        self.assertIsNone(observ_hermes._runtime)


if __name__ == "__main__":
    unittest.main()
