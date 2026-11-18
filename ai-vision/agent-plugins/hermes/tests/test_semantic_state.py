from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observ_hermes.normalize import CanonicalNormalizer
from observ_hermes.semantic import MemorySink, SemanticConfig, SemanticEngine


class Scenario:
    def __init__(self) -> None:
        self.normalizer = CanonicalNormalizer()
        self.sink = MemorySink()
        self.engine = SemanticEngine(
            SemanticConfig(username="tester", user_id="user-1"), self.sink
        )
        self.clock = 1_750_000_000.0

    def emit(self, hook: str, **raw):
        self.clock += 0.1
        raw.setdefault("telemetry_schema_version", "hermes.observer.v1")
        raw.setdefault("session_id", "session-1")
        event = self.normalizer.normalize(hook, raw, timestamp=self.clock)
        self.engine.apply(event)
        return event

    def start_turn(self) -> None:
        self.emit("on_session_start", model="model", platform="cli")
        self.emit(
            "pre_llm_call",
            turn_id="turn-1",
            task_id="task-1",
            model="model",
            user_message="hello",
            conversation_history=[],
        )


class SemanticStateTests(unittest.TestCase):
    def test_happy_path_topology_events_metrics_and_cleanup(self) -> None:
        scenario = Scenario()
        scenario.start_turn()
        scenario.emit(
            "pre_api_request",
            turn_id="turn-1",
            task_id="task-1",
            api_request_id="opaque/request/1",
            model="model",
            provider="provider",
            request_messages=[{"role": "user", "content": "hello"}],
            request={"body": {"system": "source system instructions"}},
            started_at=scenario.clock,
        )
        scenario.emit(
            "post_api_request",
            turn_id="turn-1",
            task_id="task-1",
            api_request_id="opaque/request/1",
            model="model",
            provider="provider",
            api_duration=0.125,
            response_model="model",
            finish_reason="stop",
            usage={
                "input_tokens": 11,
                "cache_read_tokens": 13,
                "cache_write_tokens": 2,
                "prompt_tokens": 26,
                "output_tokens": 7,
            },
            assistant_message={"role": "assistant", "content": "done"},
        )
        scenario.emit(
            "pre_tool_call",
            turn_id="turn-1",
            task_id="task-1",
            api_request_id="opaque/request/1",
            tool_call_id="tool-1",
            tool_name="Bash",
            args={"command": "printf ok"},
        )
        scenario.emit(
            "post_tool_call",
            turn_id="turn-1",
            task_id="task-1",
            api_request_id="opaque/request/1",
            tool_call_id="tool-1",
            tool_name="Bash",
            result="ok",
            duration_ms=25,
            status="success",
        )
        scenario.emit(
            "subagent_start",
            parent_session_id="session-1",
            parent_turn_id="turn-1",
            child_session_id="child-session",
            child_subagent_id="child-agent",
            child_role="researcher",
            child_goal="inspect",
        )
        scenario.emit(
            "subagent_stop",
            parent_session_id="session-1",
            parent_turn_id="turn-1",
            child_session_id="child-session",
            child_role="researcher",
            child_status="completed",
            child_summary="done",
            duration_ms=50,
        )
        scenario.emit(
            "post_llm_call",
            turn_id="turn-1",
            task_id="task-1",
            model="model",
            user_message="hello",
            assistant_response="done",
            conversation_history=[],
        )
        scenario.emit(
            "on_session_end",
            turn_id="turn-1",
            task_id="task-1",
            completed=True,
            interrupted=False,
        )
        scenario.emit("on_session_finalize", reason="shutdown", platform="cli")

        starts = scenario.sink.of_type("span.start")
        by_kind = {item.span_kind: item for item in starts}
        turn = by_kind["CHAT"]
        self.assertEqual(by_kind["LLM"].parent_key, turn.key)
        self.assertEqual(
            by_kind["LLM"].attributes["gen_ai.system_instructions"],
            '"source system instructions"',
        )
        self.assertEqual(by_kind["TOOL"].parent_key, turn.key)
        self.assertEqual(by_kind["AGENT"].parent_key, turn.key)
        self.assertEqual(
            by_kind["AGENT"].attributes["gen_ai.operation.name"], "invoke_agent"
        )

        llm_end = next(
            item
            for item in scenario.sink.of_type("span.end")
            if item.key == by_kind["LLM"].key
        )
        self.assertEqual(llm_end.attributes["gen_ai.usage.input_tokens"], 26)
        self.assertEqual(
            llm_end.attributes["gen_ai.usage.details.cache_read_input_tokens"], 13
        )
        api_event = next(
            item
            for item in scenario.sink.of_type("event")
            if item.name == "api_request"
        )
        self.assertEqual(api_event.attributes["gen_ai.usage.input_tokens"], 26)
        self.assertEqual(api_event.attributes["gen_ai.usage.total_tokens"], 33)
        self.assertEqual(api_event.attributes["agentic.usage.fresh_input_tokens"], 11)
        self.assertEqual(api_event.attributes["agentic.usage.cache_read_tokens"], 13)
        self.assertEqual(api_event.attributes["agentic.usage.cache_write_tokens"], 2)

        event_names = [item.name for item in scenario.sink.of_type("event")]
        shared_events = {
            "session_start",
            "session_end",
            "user_prompt",
            "api_request",
            "tool_call",
            "tool_result",
        }
        hermes_only_events = {"subagent_spawned", "subagent_ended"}
        self.assertEqual(set(event_names), shared_events | hermes_only_events)
        self.assertTrue(
            all(
                event_names.count(name) == 1
                for name in shared_events | hermes_only_events
            )
        )
        self.assertFalse({"tool_decision", "compaction_result"} & set(event_names))
        for emitted_event in scenario.sink.of_type("event"):
            self.assertEqual(emitted_event.attributes["event.name"], emitted_event.name)
            self.assertEqual(
                emitted_event.attributes["gen_ai.conversation.id"], "session-1"
            )
            self.assertEqual(emitted_event.attributes["gen_ai.chat.id"], "turn-1")
            self.assertEqual(emitted_event.attributes["agentic.turn.id"], "turn-1")
            self.assertEqual(emitted_event.attributes["agentic.runtime.name"], "hermes")

        tool_call_event = next(
            item for item in scenario.sink.of_type("event") if item.name == "tool_call"
        )
        self.assertNotIn("duration_ms", tool_call_event.attributes)
        self.assertNotIn("gen_ai.tool.call.result", tool_call_event.attributes)
        self.assertNotIn("error.type", tool_call_event.attributes)
        tool_result_event = next(
            item
            for item in scenario.sink.of_type("event")
            if item.name == "tool_result"
        )
        self.assertEqual(tool_result_event.attributes["duration_ms"], 25.0)
        self.assertEqual(
            tool_result_event.attributes["gen_ai.tool.call.result"], '"ok"'
        )

        for span_start in starts:
            self.assertEqual(
                span_start.attributes["gen_ai.conversation.id"], "session-1"
            )
            self.assertEqual(span_start.attributes["gen_ai.chat.id"], "turn-1")
            self.assertEqual(span_start.attributes["agentic.turn.id"], "turn-1")

        metric_names = [item.name for item in scenario.sink.of_type("metric")]
        self.assertCountEqual(
            metric_names,
            [
                "agent.session.count",
                "agent.token.usage",
                "agent.token.usage",
                "agent.cache.read_tokens",
                "agent.cache.write_tokens",
                "agent.api_request.count",
                "agent.api_request.duration",
                "agent.tool_call.count",
                "agent.tool_call.duration",
            ],
        )
        token_metrics = [
            item
            for item in scenario.sink.of_type("metric")
            if item.name == "agent.token.usage"
        ]
        self.assertEqual(
            {(item.attributes["type"], item.value) for item in token_metrics},
            {("input", 11), ("output", 7)},
        )
        self.assertTrue(
            all(item.attributes["ant.agent.type"] == "hermes" for item in token_metrics)
        )
        cache_metrics = {
            item.name: item
            for item in scenario.sink.of_type("metric")
            if item.name.startswith("agent.cache.")
        }
        self.assertEqual(cache_metrics["agent.cache.read_tokens"].value, 13)
        self.assertEqual(cache_metrics["agent.cache.write_tokens"].value, 2)
        self.assertEqual(
            cache_metrics["agent.cache.read_tokens"].attributes,
            {"gen_ai.request.model": "model", "ant.agent.type": "hermes"},
        )
        self.assertNotIn(
            "agent.tool_call.count",
            [
                item.name
                for item in scenario.sink.observations
                if item.type == "metric"
                and item.timestamp == scenario.sink.of_type("event")[-2].timestamp
            ],
        )
        self.assertEqual(
            scenario.engine.state.pending_counts(),
            {
                "sessions": 0,
                "turns": 0,
                "api_requests": 0,
                "tools": 0,
                "subagents": 0,
            },
        )

    def test_api_and_tool_errors_emit_one_bounded_error_metric(self) -> None:
        scenario = Scenario()
        scenario.start_turn()
        scenario.emit(
            "pre_api_request",
            turn_id="turn-1",
            api_request_id="api-1",
            model="model",
            provider="provider",
            started_at=scenario.clock,
        )
        scenario.emit(
            "api_request_error",
            turn_id="turn-1",
            api_request_id="api-1",
            model="model",
            provider="provider",
            api_duration=0.2,
            status_code=429,
            reason="rate_limit",
            error={"type": "RateLimitError", "message": "slow down"},
        )
        scenario.emit(
            "pre_tool_call",
            turn_id="turn-1",
            tool_call_id="tool-1",
            tool_name="mcp__server__read",
            args={},
        )
        terminal = scenario.emit(
            "post_tool_call",
            turn_id="turn-1",
            tool_call_id="tool-1",
            tool_name="mcp__server__read",
            duration_ms=9,
            status="error",
            error_type="ToolError",
            error_message="failed",
            result={"error": "failed"},
        )
        scenario.engine.apply(terminal)

        errors = [
            item
            for item in scenario.sink.of_type("metric")
            if item.name.endswith("error.count")
        ]
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0].attributes["agentic.error.category"], "rate_limit")
        self.assertIn(
            errors[1].attributes["agentic.error.category"],
            {
                "tool_error",
                "validation",
                "permission",
                "timeout",
                "cancelled",
            },
        )
        tool_count = [
            item
            for item in scenario.sink.of_type("metric")
            if item.name == "agent.tool_call.count"
        ]
        self.assertEqual(len(tool_count), 1)
        diagnostics = [item.name for item in scenario.sink.of_type("diagnostic")]
        self.assertIn("duplicate_canonical_event", diagnostics)
        self.assertFalse(
            any(
                item.name
                in {
                    "agent.token.usage",
                    "agent.cache.read_tokens",
                    "agent.cache.write_tokens",
                }
                for item in scenario.sink.of_type("metric")
            )
        )

    def test_zero_or_missing_token_cache_values_do_not_emit_counter_rows(self) -> None:
        scenario = Scenario()
        scenario.start_turn()
        scenario.emit(
            "pre_api_request",
            turn_id="turn-1",
            api_request_id="api-1",
            model="model",
            provider="provider",
        )
        scenario.emit(
            "post_api_request",
            turn_id="turn-1",
            api_request_id="api-1",
            model="model",
            provider="provider",
            api_duration=0.1,
            usage={
                "input_tokens": 5,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            },
        )
        metrics = scenario.sink.of_type("metric")
        token_metrics = [item for item in metrics if item.name == "agent.token.usage"]
        self.assertEqual(
            [(item.attributes["type"], item.value) for item in token_metrics],
            [
                ("input", 5),
            ],
        )
        self.assertFalse(any(item.name.startswith("agent.cache.") for item in metrics))

    def test_session_count_emits_once_without_requiring_a_turn(self) -> None:
        scenario = Scenario()
        event = scenario.emit("on_session_start", model="model", platform="cli")
        scenario.engine.apply(event)
        session_metrics = [
            item
            for item in scenario.sink.of_type("metric")
            if item.name == "agent.session.count"
        ]
        self.assertEqual(len(session_metrics), 1)
        self.assertEqual(
            session_metrics[0].attributes,
            {
                "ant.agent.type": "hermes",
                "ant.username": "tester",
            },
        )

    def test_synthetic_tool_id_persists_across_start_and_terminal(self) -> None:
        scenario = Scenario()
        scenario.start_turn()
        start = scenario.emit(
            "pre_tool_call",
            turn_id="turn-1",
            task_id="task-1",
            tool_name="Read",
            args={"path": "README.md"},
        )
        terminal = scenario.emit(
            "post_tool_call",
            turn_id="turn-1",
            task_id="task-1",
            tool_name="Read",
            result="ok",
            duration_ms=3,
        )
        call_id = start.correlation["tool_call_id"]
        self.assertTrue(call_id.startswith("synthetic-"))
        self.assertEqual(terminal.correlation["tool_call_id"], call_id)
        tool_events = [
            item
            for item in scenario.sink.of_type("event")
            if item.name.startswith("tool_")
        ]
        self.assertEqual(
            {item.attributes["gen_ai.tool.call.id"] for item in tool_events}, {call_id}
        )
        self.assertTrue(
            all(
                item.attributes["agentic.tool.call_id.synthetic"] == "true"
                for item in tool_events
            )
        )

    def test_parallel_tools_complete_out_of_order_without_parent_or_count_drift(
        self,
    ) -> None:
        scenario = Scenario()
        scenario.start_turn()
        for call_id, name in (("a", "Read"), ("b", "Bash")):
            scenario.emit(
                "pre_tool_call",
                turn_id="turn-1",
                task_id="task-1",
                tool_call_id=call_id,
                tool_name=name,
                args={},
            )
        for call_id, name in (("b", "Bash"), ("a", "Read")):
            scenario.emit(
                "post_tool_call",
                turn_id="turn-1",
                task_id="task-1",
                tool_call_id=call_id,
                tool_name=name,
                result="ok",
                duration_ms=1,
            )
        starts = [
            item
            for item in scenario.sink.of_type("span.start")
            if item.span_kind == "TOOL"
        ]
        self.assertEqual(len({item.parent_key for item in starts}), 1)
        self.assertEqual(
            len(
                [
                    item
                    for item in scenario.sink.of_type("metric")
                    if item.name == "agent.tool_call.count"
                ]
            ),
            2,
        )
        self.assertEqual(scenario.engine.state.pending_counts()["tools"], 0)

    def test_ambiguous_idless_same_name_terminal_is_diagnostic_not_false_match(
        self,
    ) -> None:
        scenario = Scenario()
        scenario.start_turn()
        for _ in range(2):
            scenario.emit(
                "pre_tool_call",
                turn_id="turn-1",
                task_id="task-1",
                tool_name="Read",
                args={"path": "same"},
            )
        terminal = scenario.emit(
            "post_tool_call",
            turn_id="turn-1",
            task_id="task-1",
            tool_name="Read",
            result="unknown",
            duration_ms=1,
        )
        self.assertEqual(len(terminal.correlation["tool_match_ambiguous"]), 2)
        self.assertIn(
            "ambiguous_tool_terminal",
            [item.name for item in scenario.sink.of_type("diagnostic")],
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in scenario.sink.of_type("metric")
                    if item.name == "agent.tool_call.count"
                ]
            ),
            0,
        )
        self.assertEqual(scenario.engine.state.pending_counts()["tools"], 2)
        scenario.emit("on_session_reset", reason="new_session")
        self.assertEqual(scenario.engine.state.pending_counts()["tools"], 0)

    def test_history_shrink_never_infers_compaction_and_session_terminal_is_idempotent(
        self,
    ) -> None:
        scenario = Scenario()
        scenario.start_turn()
        scenario.emit(
            "pre_llm_call",
            turn_id="turn-1",
            task_id="task-1",
            model="model",
            user_message="again",
            conversation_history=[],
        )
        scenario.emit("on_session_end", turn_id="turn-1", completed=True)
        terminal = scenario.emit("on_session_finalize", reason="shutdown")
        scenario.engine.apply(terminal)
        self.assertFalse(
            any("compaction" in item.name for item in scenario.sink.observations)
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in scenario.sink.of_type("event")
                    if item.name == "session_end"
                ]
            ),
            1,
        )
        self.assertIn(
            "duplicate_canonical_event",
            [item.name for item in scenario.sink.of_type("diagnostic")],
        )

    def test_two_turn_identity_is_session_scoped_and_terminal_events_anchor_first_last_turn(  # noqa: E501
        self,
    ) -> None:
        scenario = Scenario()
        scenario.emit("on_session_start", model="model", platform="cli")
        scenario.emit(
            "pre_llm_call",
            turn_id="turn-1",
            task_id="task-1",
            model="model",
            user_message="one",
            conversation_history=[],
        )
        session = scenario.engine.state.sessions["session-1"]
        self.assertEqual(session.first_turn_id, "turn-1")
        self.assertEqual(session.last_turn_id, "turn-1")
        scenario.emit("on_session_end", turn_id="turn-1", completed=True)
        scenario.emit(
            "pre_llm_call",
            turn_id="turn-2",
            task_id="task-2",
            model="model",
            user_message="two",
            conversation_history=[],
        )
        self.assertEqual(session.first_turn_id, "turn-1")
        self.assertEqual(session.last_turn_id, "turn-2")
        scenario.emit("on_session_end", turn_id="turn-2", completed=True)
        scenario.emit("on_session_finalize", reason="shutdown")

        turns = [
            item
            for item in scenario.sink.of_type("span.start")
            if item.span_kind == "CHAT"
        ]
        self.assertEqual(
            [item.attributes["gen_ai.conversation.id"] for item in turns],
            [
                "session-1",
                "session-1",
            ],
        )
        self.assertEqual(
            [item.attributes["gen_ai.chat.id"] for item in turns], ["turn-1", "turn-2"]
        )
        self.assertEqual(
            [item.attributes["agentic.turn.id"] for item in turns], ["turn-1", "turn-2"]
        )

        session_start = next(
            item
            for item in scenario.sink.of_type("event")
            if item.name == "session_start"
        )
        session_end = next(
            item
            for item in scenario.sink.of_type("event")
            if item.name == "session_end"
        )
        self.assertEqual(session_start.key, "turn:session-1:turn-1")
        self.assertEqual(session_start.attributes["gen_ai.chat.id"], "turn-1")
        self.assertEqual(session_start.attributes["agentic.turn.id"], "turn-1")
        self.assertEqual(session_end.key, "turn:session-1:turn-2")
        self.assertEqual(session_end.attributes["gen_ai.chat.id"], "turn-2")
        self.assertEqual(session_end.attributes["agentic.turn.id"], "turn-2")
        self.assertEqual(session_end.attributes["gen_ai.conversation.id"], "session-1")

    def test_missing_host_turn_identity_uses_stable_collision_safe_canonical_fallback(
        self,
    ) -> None:
        def first_turn_id() -> str:
            scenario = Scenario()
            scenario.emit("on_session_start", model="model", platform="cli")
            scenario.emit(
                "pre_llm_call",
                model="model",
                user_message="hello",
                conversation_history=[],
            )
            turn = scenario.engine.state.sessions["session-1"].active_turn
            assert turn is not None
            return turn.source_id

        first = first_turn_id()
        second = first_turn_id()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^canonical-turn-[0-9a-f]{24}$")

        scenario = Scenario()
        scenario.emit("on_session_start", model="model", platform="cli")
        scenario.emit(
            "pre_llm_call", model="model", user_message="one", conversation_history=[]
        )
        turn_one = scenario.engine.state.sessions["session-1"].active_turn
        assert turn_one is not None
        scenario.emit("on_session_end", completed=True)
        scenario.emit(
            "pre_llm_call", model="model", user_message="two", conversation_history=[]
        )
        turn_two = scenario.engine.state.sessions["session-1"].active_turn
        assert turn_two is not None
        self.assertNotEqual(turn_one.source_id, turn_two.source_id)

    def test_reset_cancels_pending_operations_once_and_clears_every_registry(
        self,
    ) -> None:
        scenario = Scenario()
        scenario.start_turn()
        scenario.emit(
            "pre_api_request",
            turn_id="turn-1",
            api_request_id="api-pending",
            model="model",
            provider="provider",
        )
        scenario.emit(
            "pre_tool_call",
            turn_id="turn-1",
            tool_call_id="tool-pending",
            tool_name="Bash",
            args={"command": "sleep 10"},
        )
        scenario.emit(
            "subagent_start",
            parent_session_id="session-1",
            parent_turn_id="turn-1",
            child_session_id="child-pending",
            child_subagent_id="agent-pending",
            child_role="executor",
        )
        scenario.emit("on_session_reset", reason="new_session")

        self.assertEqual(
            scenario.engine.state.pending_counts(),
            {
                "sessions": 0,
                "turns": 0,
                "api_requests": 0,
                "tools": 0,
                "subagents": 0,
            },
        )
        metrics = scenario.sink.of_type("metric")
        self.assertEqual(
            len([item for item in metrics if item.name == "agent.api_request.count"]), 1
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in metrics
                    if item.name == "agent.api_request.error.count"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [item for item in metrics if item.name == "agent.api_request.duration"]
            ),
            1,
        )
        self.assertEqual(
            len([item for item in metrics if item.name == "agent.tool_call.count"]), 1
        )
        self.assertEqual(
            len(
                [item for item in metrics if item.name == "agent.tool_call.error.count"]
            ),
            1,
        )
        self.assertEqual(
            len([item for item in metrics if item.name == "agent.tool_call.duration"]),
            1,
        )
        self.assertTrue(
            all(
                item.attributes.get("agentic.error.category") == "cancelled"
                for item in metrics
                if item.name.endswith("error.count")
            )
        )
        sequences = [
            item.attributes["event.sequence"] for item in scenario.sink.of_type("event")
        ]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

    def test_reset_for_rotated_unstarted_session_does_not_fabricate_session_end(
        self,
    ) -> None:
        scenario = Scenario()
        scenario.emit("on_session_reset", reason="new_session")
        self.assertFalse(scenario.sink.of_type("event"))
        self.assertEqual(
            [item.name for item in scenario.sink.of_type("diagnostic")],
            ["reset_without_active_session"],
        )
        self.assertEqual(scenario.engine.state.pending_counts()["sessions"], 0)


if __name__ == "__main__":
    unittest.main()
