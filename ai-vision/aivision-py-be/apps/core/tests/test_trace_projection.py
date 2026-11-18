import json
from itertools import permutations
import unittest

from apps.core.trace_query.semantics.observations import (
    iso_timestamp,
    project_observation,
    project_trace,
    timestamp_ns,
)


def span(identifier="root", parent="", kind="span", **attributes):
    return {
        "trace_id": "trace-1",
        "span_id": identifier,
        "parent_span_id": parent,
        "name": identifier,
        "start_time_unix_nano": "2026-10-03T01:02:03.123456789Z",
        "end_time_unix_nano": "2026-10-03T01:02:04.123456790Z",
        "attributes": {"langfuse.observation.type": kind, **attributes},
        "status": {"code": 2, "message": "failed"},
    }


class TraceProjectionTests(unittest.TestCase):
    def test_child_trace_update_overrides_unannotated_wrapper_only_at_trace_level(self):
        root = span(**{"langfuse.observation.input": '"wrapper-input"',
                       "langfuse.observation.output": '"wrapper-output"',
                       "langfuse.observation.metadata.wrapper": "observation-only"})
        child = span("child", "root", **{
            "langfuse.trace.name": "business-trace", "langfuse.trace.input": '{"question":"actual"}',
            "langfuse.trace.output": '{"answer":"actual"}', "langfuse.user.id": "business-user",
            "langfuse.session.id": "conversation", "langfuse.trace.metadata.tier": "gold",
            "langfuse.trace.tags": ("sdk",), "langfuse.observation.input": '"child-input"',
        })
        child["resource_attributes"] = {"aivision.agent.id": "trusted-agent"}
        result = project_trace({"traceId": "trace-1", "spans": [child, root]}, project_id="p")
        self.assertEqual({key: result[key] for key in ("name", "input", "output", "userId", "sessionId", "metadata", "tags")}, {
            "name": "business-trace", "input": {"question": "actual"}, "output": {"answer": "actual"},
            "userId": "business-user", "sessionId": "conversation", "metadata": {"tier": "gold"}, "tags": ["sdk"],
        })
        self.assertEqual(result["rootObservationId"], "root")
        self.assertEqual(result["observations"][1]["input"], "wrapper-input")
        self.assertEqual(result["observations"][0]["resourceAttributes"]["aivision.agent.id"], "trusted-agent")

    def test_trace_updates_merge_per_field_in_completion_start_identity_order(self):
        root = span(**{"langfuse.trace.name": "old-name", "langfuse.trace.metadata": '{"retained":1,"tier":"old"}'})
        root["end_time_unix_nano"] = "2026-10-03T01:02:04Z"
        early = span("a", "root", **{"langfuse.trace.input": '"input-from-a"',
                                     "langfuse.trace.output": '"old-output"', "langfuse.trace.metadata.tier": "silver"})
        late = span("z", "root", **{"langfuse.trace.name": "new-name", "langfuse.trace.output": '"new-output"',
                                     "langfuse.user.id": "new-user", "langfuse.trace.tags": [],
                                     "langfuse.trace.metadata.tier": "gold", "langfuse.trace.metadata.added": False})
        for rows in permutations([root, early, late]):
            result = project_trace({"traceId": "trace-1", "spans": rows}, project_id="p")
            self.assertEqual(result["name"], "new-name")
            self.assertEqual(result["input"], "input-from-a")
            self.assertEqual(result["output"], "new-output")
            self.assertEqual(result["userId"], "new-user")
            self.assertEqual(result["tags"], [])
            self.assertEqual(result["metadata"], {"retained": 1, "tier": "gold", "added": False})

    def test_root_only_trace_update_keeps_scalar_identifiers_as_strings(self):
        root = span(**{"langfuse.trace.name": "false", "user.id": "123", "langfuse.trace.tags": ["root"]})
        result = project_trace({"traceId": "trace-1", "spans": [root]}, project_id="p")
        self.assertEqual(result["name"], "false")
        self.assertEqual(result["userId"], "123")
        self.assertEqual(result["tags"], ["root"])

    def test_child_trace_io_preserves_explicit_empty_false_zero_and_null(self):
        for value in (None, False, 0, "", [], {}):
            root = span(**{"langfuse.observation.input": '"fallback"', "langfuse.observation.output": '"fallback"'})
            child = span("child", "root", **{f"langfuse.trace.{field}": json.dumps(value) for field in ("input", "output")})
            result = project_trace({"traceId": "trace-1", "spans": [root, child]}, project_id="p")
            for field in ("input", "output"):
                self.assertEqual(result[field], value)
                self.assertIs(type(result[field]), type(value))

    def test_explicit_relay_business_io_preserves_json_types_and_standard_precedence(self):
        for value in ({"receipt": "r1"}, 0, False, "0", '{"x": 1}', None):
            with self.subTest(value=value):
                attrs = {f"aivision.request.{field}": json.dumps(value)
                         for field in ("input", "output")}
                result = project_trace({"traceId": "trace-1", "spans": [span(**attrs)]}, project_id="p")
                for field in ("input", "output"):
                    self.assertEqual(result[field], value)
                    self.assertIs(type(result[field]), type(value))
                attrs["langfuse.observation.input"] = '"langfuse"'
                self.assertEqual(project_observation(span(**attrs), project_id="p")["input"], "langfuse")
                attrs["gen_ai.input.messages"] = '[]'
                self.assertEqual(project_observation(span(**attrs), project_id="p")["input"], [])

    def test_legacy_agent_span_kind_and_raw_status_are_preserved(self):
        row = span()
        row["attributes"] = {
            "gen_ai.span.kind": "LLM",
            "gen_ai.system": "anthropic",
            "gen_ai.usage.total_tokens": 9,
        }
        row["status"] = {}
        row["status_code"] = 2
        row["status_message"] = "failed upstream"
        result = project_observation(row, project_id="p")
        self.assertEqual(result["type"], "GENERATION")
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(
            result["otelStatus"], {"code": 2, "message": "failed upstream"}
        )
        self.assertEqual(result["level"], "DEFAULT")
        row["attributes"]["gen_ai.span.kind"] = "TOOL"
        self.assertEqual(project_observation(row, project_id="p")["type"], "TOOL")

    def test_resource_shapes_keep_trusted_identity_and_explicit_standard_fields_win(
        self,
    ):
        for resource in (
            {"resource": {"aivision.agent.id": "trusted"}},
            {"resource": {"attributes": {"aivision.agent.id": "trusted"}}},
            {"resource.attributes.aivision.agent.id": "trusted"},
        ):
            row = span(
                kind="generation",
                **{
                    "gen_ai.operation.name": "embeddings",
                    "gen_ai.input.messages": '[{"role":"user","content":"explicit"}]',
                    "langfuse.observation.input": '"alias"',
                },
            )
            row.update(resource)
            result = project_observation(row, project_id="p")
            self.assertEqual(
                result["resourceAttributes"]["aivision.agent.id"], "trusted"
            )
            self.assertEqual(result["type"], "EMBEDDING")
            self.assertEqual(result["input"][0]["content"], "explicit")

    def test_derived_standard_io_retains_original_structured_payload(self):
        result = project_observation(
            span(
                **{
                    "langfuse.observation.input": '{"messages":["original"],"custom":1}',
                    "gen_ai.input.messages": '[{"role":"user","content":"original"}]',
                    "aivision.normalization.sources": '{"gen_ai.input.messages":"langfuse.observation.input"}',
                }
            ),
            project_id="p",
        )
        self.assertEqual(result["input"], {"messages": ["original"], "custom": 1})

    def test_timestamp_roundtrip_preserves_nanoseconds_and_offsets(self):
        text = "2026-10-03T01:02:03.123456789Z"
        self.assertEqual(iso_timestamp(timestamp_ns(text)), text)
        self.assertEqual(
            timestamp_ns("2026-10-03T09:02:03.123456789+08:00"), timestamp_ns(text)
        )

    def test_root_io_is_structured_and_independent_from_child(self):
        result = project_trace(
            {
                "traceId": "trace-1",
                "spans": [
                    span(
                        **{
                            "langfuse.observation.input": '{"question":"hi"}',
                            "langfuse.observation.output": '"answer"',
                            "session.id": "session-1",
                        }
                    ),
                    span(
                        "llm",
                        "root",
                        "generation",
                        **{
                            "langfuse.observation.output": '"child"',
                            "gen_ai.usage.input_tokens": 3,
                            "gen_ai.usage.output_tokens": 2,
                        },
                    ),
                ],
            },
            project_id="project-1",
        )
        self.assertEqual(result["input"], {"question": "hi"})
        self.assertEqual(result["output"], "answer")
        self.assertEqual(result["sessionId"], "session-1")
        self.assertEqual(result["rootObservationId"], "root")
        self.assertEqual(result["usage"]["total"], 5)
        observations = {item["id"]: item for item in result["observations"]}
        self.assertEqual(observations["llm"]["parentObservationId"], "root")
        self.assertEqual(observations["root"]["otelStatus"]["code"], 2)
        self.assertEqual(observations["root"]["level"], "DEFAULT")

    def test_multi_roots_and_orphans_are_explicit_without_rewriting_parent(self):
        result = project_trace(
            {"traceId": "trace-1", "spans": [span("b", "missing"), span("a")]},
            project_id="p",
        )
        self.assertEqual(result["rootObservationId"], "a")
        self.assertEqual(result["rootSelectionReason"], "root")
        self.assertEqual(result["missingParentIds"], ["missing"])
        self.assertEqual(result["observations"][1]["parentObservationId"], "missing")

    def test_unknown_type_and_raw_values_survive(self):
        result = project_trace(
            {
                "traceId": "trace-1",
                "spans": [
                    span(
                        kind="private-type", **{"langfuse.observation.level": "WARNING"}
                    )
                ],
            },
            project_id="p",
        )
        observation = result["observations"][0]
        self.assertEqual(observation["type"], "PRIVATE-TYPE")
        self.assertEqual(observation["level"], "WARNING")
        self.assertEqual(
            observation["attributes"]["langfuse.observation.type"], "private-type"
        )
        self.assertIsNone(observation["usage"]["total"])

    def test_v4_root_metadata_and_legacy_tool_io_have_canonical_values(self):
        row = span(**{"langfuse.observation.metadata.suite": "contract"})
        result = project_trace({"traceId": "trace-1", "spans": [row]}, project_id="p")
        self.assertEqual(result["metadata"], {"suite": "contract"})
        row["attributes"] = {
            "gen_ai.span.kind": "TOOL",
            "gen_ai.tool.call.arguments": '{"q":"hello"}',
            "gen_ai.tool.call.result": '{"answer":"ok"}',
        }
        tool = project_observation(row, project_id="p")
        self.assertEqual(tool["input"], {"q": "hello"})
        self.assertEqual(tool["output"], {"answer": "ok"})


def skill_read_span(identifier="bash", parent="root", command=None):
    return span(
        identifier,
        parent,
        **{
            "gen_ai.span.kind": "TOOL",
            "gen_ai.tool.call.name": "Bash",
            "gen_ai.tool.call.arguments": json.dumps(
                {"cmd": command or "cat /root/.codex/skills/visualize/SKILL.md"}
            ),
        },
    )


class TraceSkillTagTests(unittest.TestCase):
    """Derived skill usage must reach langfuse.trace.tags like native tags do.

    The collector only tags natively reported tool types; the projection
    merges the shared recognition's findings so tags consumers (preview
    Tags section, list rows, facets, tag filters) see the same skills the
    usage scanner counts.
    """

    def test_derived_skill_read_merges_into_trace_tags(self):
        result = project_trace(
            {"traceId": "trace-1", "spans": [span(), skill_read_span()]},
            project_id="p",
        )
        self.assertEqual(result["tags"], ["skill:visualize"])
        root = next(o for o in result["observations"] if o["id"] == "root")
        self.assertEqual(
            root["attributes"]["langfuse.trace.tags"], ["skill:visualize"]
        )
        self.assertEqual(root["tags"], ["skill:visualize"])

    def test_native_tags_are_preserved_and_derived_appended(self):
        root = span(**{"langfuse.trace.tags": ["mcp:codex_app.load"]})
        result = project_trace(
            {"traceId": "trace-1", "spans": [root, skill_read_span()]},
            project_id="p",
        )
        self.assertEqual(result["tags"], ["mcp:codex_app.load", "skill:visualize"])

    def test_fallback_invocations_and_non_reader_commands_are_excluded(self):
        fallback = span(
            "native",
            **{
                "agentic.tool.type": "skill",
                "agentic.tool.name": "openai-docs",
                "gen_ai.tool.call.arguments": '{"source":"command_path_fallback"}',
            },
        )
        echo = skill_read_span("echo", command='echo "cat /skills/visualize/SKILL.md"')
        result = project_trace(
            {"traceId": "trace-1", "spans": [span(), fallback, echo]},
            project_id="p",
        )
        self.assertEqual(result["tags"], [])

    def test_trace_without_skill_shapes_keeps_tags_untouched(self):
        root = span(**{"langfuse.trace.tags": ["sdk"]})
        result = project_trace({"traceId": "trace-1", "spans": [root]}, project_id="p")
        self.assertEqual(result["tags"], ["sdk"])
        self.assertEqual(
            result["observations"][0]["attributes"]["langfuse.trace.tags"], ["sdk"]
        )
