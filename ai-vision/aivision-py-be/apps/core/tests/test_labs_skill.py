import json
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import SkillConfig, Space
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.trace_query.langfuse.provider import LangfuseTraceProvider
from apps.core.trace_query.service import TraceQueryService
from apps.core.views.labs_skill import (
    _sanitize_incoming,
    _scan_skill_window,
    _skill_span_info,
)

TOOL = {"gen_ai.span.kind": "TOOL"}


class SkillSpanInfoTests(SimpleTestCase):
    def test_window_parses_each_span_once_even_with_nested_skills(self):
        spans = [
            {
                "span_id": str(index),
                "parent_span_id": str(index - 1) if index else "",
                "attributes": {
                    "agentic.tool.type": "skill",
                    "agentic.tool.name": f"skill-{index % 2}",
                },
            }
            for index in range(50)
        ]
        with patch(
            "apps.core.views.labs_skill._skill_span_info", wraps=_skill_span_info
        ) as recognize:
            result = _scan_skill_window([{"trace_id": "trace", "spans": spans}], "codex")
        self.assertEqual(recognize.call_count, len(spans))
        self.assertEqual(result["overview"]["calls"], 50)
        self.assertEqual(sum(edge["calls"] for edge in result["relations"]), 49)

    def test_native_tool_type_wins(self):
        span = {
            "attributes": {
                **TOOL,
                "agentic.tool.type": "skill",
                "agentic.tool.name": "visualize",
                "agentic.tool.id": "skill-visualize",
            }
        }
        self.assertEqual(_skill_span_info(span), ("visualize", False))

    def test_aicoder_storage_columns_report_natively(self):
        span = {"attributes": {"agentic.skill.name": "deploy"}}
        self.assertEqual(_skill_span_info(span), ("deploy", False))

    def test_plugin_reported_derived_boolean_is_preserved(self):
        span = {
            "attributes": {
                **TOOL,
                "agentic.tool.type": "skill",
                "agentic.tool.name": "weather",
                "agentic.tool.derived": True,
            }
        }
        self.assertEqual(_skill_span_info(span), ("weather", True))

    def test_derived_exec_shape(self):
        span = {
            "attributes": {
                **TOOL,
                "agentic.tool.call.arguments": "cat /root/.codex/skills/visualize/SKILL.md",
            }
        }
        self.assertEqual(_skill_span_info(span), ("visualize", True))

    def test_derived_read_file_shape_with_escaped_slashes(self):
        span = {
            "attributes": {
                **TOOL,
                "gen_ai.tool.call.arguments": '{"file_path":"\\/skills\\/blog-post\\/SKILL.md"}',
            }
        }
        self.assertEqual(_skill_span_info(span), ("blog-post", True))

    def test_derived_wrapped_and_compound_reader_commands(self):
        # Real production shapes: the reader program is not the first token
        # of the whole command text.
        for arguments in (
            '{"cmd":"/bin/zsh -lc \'cat /root/.codex/skills/visualize/SKILL.md\'"}',
            '{"cmd":"bash -lc \\"cat /root/.codex/skills/visualize/SKILL.md\\" >/tmp/skill.md; wc -l /tmp/skill.md"}',
            '{"cmd":"cat /root/.codex/skills/visualize/SKILL.md | grep -n foo"}',
        ):
            span = {
                "attributes": {**TOOL, "agentic.tool.call.arguments": arguments}
            }
            self.assertEqual(
                _skill_span_info(span), ("visualize", True), arguments
            )

    def test_non_reader_shell_text_never_derives(self):
        for arguments in (
            '{"cmd":"printf \'/skills/visualize/SKILL.md\'"}',
            '{"cmd":"node /skills/visualize/SKILL.md"}',
            '{"cmd":"find /skills/visualize -name SKILL.md -print"}',
            '{"cmd":"echo \\"a; cat /skills/visualize/SKILL.md\\""}',
            "echo \"a; cat /skills/visualize/SKILL.md\"",
            "# cat /skills/visualize/SKILL.md",
        ):
            span = {
                "attributes": {**TOOL, "agentic.tool.call.arguments": arguments}
            }
            self.assertIsNone(_skill_span_info(span), arguments)

    def test_writer_tool_shapes_never_derive(self):
        for tool_name in ("write_file", "edit", "apply_patch"):
            span = {
                "attributes": {
                    **TOOL,
                    "gen_ai.tool.call.name": tool_name,
                    "gen_ai.tool.call.arguments": '{"file_path":"/skills/blog-post/SKILL.md"}',
                }
            }
            self.assertIsNone(_skill_span_info(span), tool_name)

    def test_writer_tool_name_aliases_never_derive(self):
        for key in (
            "gen_ai.tool.call.name",
            "agentic.tool.raw_name",
            "agentic.tool.name",
            "gen_ai.tool.name",
        ):
            for tool_name in (
                "write",
                "write_file",
                "writefile",
                "edit",
                "edit_file",
                "apply_patch",
                "multi_edit",
                "create_file",
                "notebook_edit",
                "str_replace",
            ):
                with self.subTest(key=key, tool_name=tool_name):
                    span = {
                        "attributes": {
                            **TOOL,
                            key: tool_name,
                            "gen_ai.tool.call.arguments": json.dumps(
                                {
                                    "path": "/skills/deploy/SKILL.md",
                                    "content": "# Updated instructions",
                                }
                            ),
                        }
                    }
                    self.assertIsNone(_skill_span_info(span))

    def test_producer_writer_shapes_never_derive(self):
        for display_name in ("write", "tool write"):
            span = {
                "attributes": {
                    **TOOL,
                    "gen_ai.tool.name": display_name,
                    "agentic.tool.type": "builtin",
                    "agentic.tool.name": "write",
                    "agentic.tool.raw_name": "write",
                    "gen_ai.tool.call.arguments": json.dumps(
                        {
                            "path": "/skills/deploy/SKILL.md",
                            "content": "# Updated instructions",
                        }
                    ),
                }
            }
            self.assertIsNone(_skill_span_info(span), display_name)

    def test_producer_reader_shape_still_derives(self):
        span = {
            "attributes": {
                **TOOL,
                "gen_ai.tool.name": "read",
                "agentic.tool.type": "builtin",
                "agentic.tool.name": "read",
                "agentic.tool.raw_name": "read",
                "gen_ai.tool.call.arguments": '{"path":"/skills/deploy/SKILL.md"}',
            }
        }
        self.assertEqual(_skill_span_info(span), ("deploy", True))

    def test_unrelated_reader_does_not_turn_a_skill_mention_into_usage(self):
        for command in (
            "echo /skills/deploy/SKILL.md; cat README.md",
            "printf /skills/deploy/SKILL.md | head -n 1",
            "cat README.md; echo /skills/deploy/SKILL.md",
        ):
            span = {
                "attributes": {
                    **TOOL,
                    "gen_ai.tool.call.arguments": json.dumps({"cmd": command}),
                }
            }
            self.assertIsNone(_skill_span_info(span), command)

    def test_name_comes_from_the_reader_instead_of_an_earlier_mention(self):
        span = {
            "attributes": {
                **TOOL,
                "gen_ai.tool.call.arguments": json.dumps(
                    {
                        "cmd": "echo /skills/wrong/SKILL.md; cat /skills/real/SKILL.md",
                    }
                ),
            }
        }
        self.assertEqual(_skill_span_info(span), ("real", True))

    def test_middleware_narration_never_matches(self):
        span = {
            "attributes": {
                "gen_ai.span.kind": "CHAIN",
                "gen_ai.tool.call.arguments": "SkillsMiddleware lists skills/x/SKILL.md",
            }
        }
        self.assertIsNone(_skill_span_info(span))

    def test_tool_result_is_never_evidence(self):
        span = {
            "attributes": {
                **TOOL,
                "agentic.tool.call.result": "271",
                "agentic.skill.output": "/skills/blog-post/SKILL.md",
            }
        }
        self.assertIsNone(_skill_span_info(span))

    def test_directory_listing_is_not_a_call(self):
        span = {
            "attributes": {
                "skills_metadata": '[{"name":"blog-post","description":"write"}]'
            }
        }
        self.assertIsNone(_skill_span_info(span))

    # Turn 11/15 reuse shape: codex replays the user message carrying its
    # skill badge link into the LLM span's input messages.
    INPUT_MESSAGES = (
        '[{"role":"user","content":"[$visualize:visualize]('
        "/Users/sunchong/.codex/plugins/cache/openai-bundled/visualize/1.0.49"
        '/skills/visualize/SKILL.md) 做一个交互式数据可视化 demo"}]'
    )

    def test_host_invocation_link_in_llm_input_messages_is_not_a_call(self):
        # A chat turn is not a skill invocation: the user message merely
        # carries the host's skill badge link (appearance), while the
        # SKILL.md read on a TOOL span is the usage evidence (execution).
        # Recognizing the turn would double-count one @-mention beside its
        # own read span.
        span = {
            "attributes": {
                "gen_ai.span.kind": "LLM",
                "gen_ai.input.messages": self.INPUT_MESSAGES,
            }
        }
        self.assertIsNone(_skill_span_info(span))

    # Plugin-inferred invocations carry no command text in their arguments,
    # so the platform cannot re-judge them; the same event re-enters through
    # the gated shape branch on the exec TOOL span instead.
    FALLBACK_ARGUMENTS = (
        '{"skill":"visualize","invocation":"$visualize",'
        '"source":"command_path_fallback","byte_range":null}'
    )

    def test_fallback_native_span_is_not_usage(self):
        span = {
            "attributes": {
                **TOOL,
                "agentic.tool.type": "skill",
                "agentic.tool.name": "visualize",
                "gen_ai.tool.call.arguments": self.FALLBACK_ARGUMENTS,
            }
        }
        self.assertIsNone(_skill_span_info(span))

    def test_semantic_native_sources_stay_authoritative(self):
        for arguments in (
            '{"skill":"visualize","source":"codex_text_element"}',
            '{"skill":"visualize","source":"legacy_markdown"}',
            None,
            "[REDACTED]",
            "cat /root/.codex/skills/visualize/SKILL.md",
        ):
            span = {
                "attributes": {
                    **TOOL,
                    "agentic.tool.type": "skill",
                    "agentic.tool.name": "visualize",
                    **(
                        {"gen_ai.tool.call.arguments": arguments}
                        if arguments is not None
                        else {}
                    ),
                }
            }
            self.assertEqual(
                _skill_span_info(span), ("visualize", False), arguments
            )

    def test_redirect_onto_skill_md_is_not_usage(self):
        for arguments in (
            '{"cmd":"cat > /root/.codex/skills/visualize/SKILL.md"}',
            '{"cmd":"cat >> /root/.codex/skills/visualize/SKILL.md"}',
            '{"cmd":"cat >\'/root/.codex/skills/visualize/SKILL.md\'"}',
            '{"cmd":"sed -i \'s/a/b/\' /root/.codex/skills/visualize/SKILL.md"}',
            '{"cmd":"echo x > /root/.codex/skills/visualize/SKILL.md"}',
        ):
            span = {
                "attributes": {**TOOL, "agentic.tool.call.arguments": arguments}
            }
            self.assertIsNone(_skill_span_info(span), arguments)

    def test_reading_then_redirecting_elsewhere_still_counts(self):
        for arguments in (
            '{"cmd":"cat /root/.codex/skills/visualize/SKILL.md > /tmp/copy.md"}',
            '{"cmd":"cat /root/.codex/skills/visualize/SKILL.md 2>/tmp/err.log"}',
            '{"cmd":"head -n 20 /root/.codex/skills/visualize/SKILL.md"}',
        ):
            span = {
                "attributes": {**TOOL, "agentic.tool.call.arguments": arguments}
            }
            self.assertEqual(
                _skill_span_info(span), ("visualize", True), arguments
            )


def _trace(trace_id: str, spans: list[dict]) -> dict:
    return {"trace_id": trace_id, "spans": spans}


def _skill_span(
    span_id: str, parent: str, name: str, start_offset_ms: int = 0
) -> dict:
    start = 1_792_108_800_000_000_000 + start_offset_ms * 1_000_000
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t",
        "attributes": {
            **TOOL,
            "agentic.tool.call.arguments": f'{{"file_path":"/skills/{name}/SKILL.md"}}',
        },
        "status": {"code": "OK"},
        "start_time_unix_nano": start,
        "end_time_unix_nano": start + 100_000_000,
    }


def _native_skill_span(
    span_id: str,
    parent: str,
    name: str,
    duration_ms: int = 500,
    arguments: str | None = None,
) -> dict:
    attributes = {
        **TOOL,
        "agentic.tool.type": "skill",
        "agentic.tool.name": name,
    }
    if arguments is not None:
        attributes["gen_ai.tool.call.arguments"] = arguments
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": "t",
        "attributes": attributes,
        "status": {"code": "OK"},
        "start_time_unix_nano": 1_792_108_800_000_000_000,
        "end_time_unix_nano": 1_792_108_800_000_000_000 + duration_ms * 1_000_000,
    }


class ScanSkillWindowTests(SimpleTestCase):
    def test_missing_duration_and_time_are_unknown_not_zero_or_epoch(self):
        span = _native_skill_span("missing", "root", "deploy")
        span.pop("start_time_unix_nano")
        span.pop("end_time_unix_nano")
        result = _scan_skill_window([_trace("t", [span])], "codex")
        skill = result["skills"][0]
        self.assertIsNone(skill["recentCalls"][0]["timestamp"])
        self.assertIsNone(skill["recentCalls"][0]["durationMs"])
        self.assertIsNone(skill["p50Ms"])
        self.assertIsNone(skill["lastSeen"])
        self.assertEqual(skill["measuredCalls"], 0)


    def test_recent_calls_sort_by_time_after_trace_grouping_and_deduplicate_ids(self):
        latest = _skill_span("latest", "root", "deploy", 1000)
        early = _skill_span("early", "root", "deploy", 100)
        middle = _skill_span("middle", "root", "deploy", 900)
        middle["trace_id"] = "other"
        result = _scan_skill_window([_trace("t", [latest, early]), _trace("other", [middle, middle])], "codex")
        self.assertEqual([call["spanId"] for call in result["skills"][0]["recentCalls"]], ["latest", "middle", "early"])
        self.assertEqual(result["skills"][0]["calls"], 3)


    def test_fractional_times_sort_numerically_and_unknown_times_stay_last(self):
        whole = _native_skill_span("whole", "root", "deploy")
        fractional = _native_skill_span("fractional", "root", "deploy")
        unknown = _native_skill_span("unknown", "root", "deploy")
        whole["start_time_unix_nano"] = "2026-10-27T00:00:00Z"
        fractional["start_time_unix_nano"] = "2026-10-27T00:00:00.500000Z"
        unknown.pop("start_time_unix_nano")
        result = _scan_skill_window([_trace("t", [whole, fractional, unknown])], "codex")
        skill = result["skills"][0]
        self.assertEqual([call["spanId"] for call in skill["recentCalls"]], ["fractional", "whole", "unknown"])
        self.assertEqual(skill["lastSeen"], "2026-10-27T00:00:00.500000Z")

    def test_provenance_counts_cover_full_window_beyond_recent_calls(self):
        traces = []
        for index in range(205):
            span = _native_skill_span(str(index), "root", "mixed")
            span["attributes"]["agentic.tool.derived"] = index < 201
            traces.append(_trace(str(index), [span]))
        result = _scan_skill_window(traces, "codex")
        self.assertEqual(len(result["skills"][0]["recentCalls"]), 200)
        self.assertEqual(result["skills"][0]["provenance"], {"inferred": 201, "reported": 4})
        self.assertEqual(result["overview"]["provenance"], {"inferred": 201, "reported": 4})

    def test_plugin_derived_load_keeps_its_derived_badge(self):
        span = _native_skill_span("load", "root", "weather", duration_ms=0)
        span["attributes"]["agentic.tool.derived"] = True
        result = _scan_skill_window([_trace("t1", [span])], "openclaw")
        self.assertTrue(result["skills"][0]["derived"])
        self.assertTrue(result["skills"][0]["recentCalls"][0]["derived"])

    def test_counts_and_derived_flag(self):
        traces = [
            _trace("t1", [_skill_span("s1", "root", "blog-post")]),
            _trace("t2", [_skill_span("s2", "root", "visualize")]),
            _trace("t3", [_skill_span("s3", "root", "blog-post")]),
        ]
        result = _scan_skill_window(traces, "codex")
        self.assertEqual(result["overview"]["skillCount"], 2)
        self.assertEqual(result["overview"]["calls"], 3)
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["blog-post"]["calls"], 2)
        self.assertEqual(by_name["visualize"]["derived"], True)
        self.assertEqual(result["relations"], [])

    def test_nearest_skill_ancestor_builds_one_edge(self):
        traces = [
            _trace(
                "t1",
                [
                    _skill_span("outer", "root", "visualize"),
                    # A plain tool layer between the two skills: still direct
                    # nesting because the walk starts from the descendant.
                    {
                        "span_id": "mid",
                        "parent_span_id": "outer",
                        "trace_id": "t1",
                        "attributes": {**TOOL, "gen_ai.tool.name": "bash"},
                    },
                    _skill_span("inner", "mid", "blog-post"),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        self.assertEqual(len(result["relations"]), 1)
        edge = result["relations"][0]
        self.assertEqual(edge["from"], "visualize")
        self.assertEqual(edge["to"], "blog-post")
        self.assertEqual(edge["calls"], 1)
        self.assertEqual(edge["traces"], 1)

    def test_no_self_edges_for_same_skill_nesting(self):
        traces = [
            _trace(
                "t1",
                [
                    _skill_span("outer", "root", "blog-post"),
                    _skill_span("inner", "outer", "blog-post"),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        self.assertEqual(result["relations"], [])

    # Usage merge: the plugin derives its native period span from the first
    # read, so a native period overlapping its own reads is the same usage —
    # the reads carry the count and the native record is not counted again.

    def test_native_period_overlapping_reads_is_not_counted_twice(self):
        traces = [
            _trace(
                "t1",
                [
                    # A period long enough to cover every read below.
                    _native_skill_span("period", "root", "visualize", duration_ms=500),
                    _skill_span("read1", "period", "visualize", start_offset_ms=50),
                    _skill_span("read2", "period", "visualize", start_offset_ms=200),
                    _skill_span("read3", "period", "visualize", start_offset_ms=400),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["visualize"]["calls"], 3)

    def test_native_without_reads_counts_once(self):
        traces = [_trace("t1", [_native_skill_span("s1", "root", "openai-docs")])]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["openai-docs"]["calls"], 1)
        self.assertEqual(by_name["openai-docs"]["derived"], False)

    def test_native_merge_is_per_skill(self):
        traces = [
            _trace(
                "t1",
                [
                    _native_skill_span("s1", "root", "typesafe-ai"),
                    _skill_span("s2", "s1", "typesafe-ai"),
                    _skill_span("s3", "root", "dws"),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["typesafe-ai"]["calls"], 1)
        self.assertEqual(by_name["dws"]["calls"], 1)

    def test_fallback_native_period_neither_counts_nor_merges(self):
        # The plugin-inferred period is rejected as evidence: the read
        # re-derives from the exec TOOL span, so it counts and the fallback
        # keeps the derived badge on.
        traces = [
            _trace(
                "t1",
                [
                    _native_skill_span(
                        "period",
                        "root",
                        "visualize",
                        duration_ms=500,
                        arguments='{"skill":"visualize","source":"command_path_fallback"}',
                    ),
                    _skill_span("read1", "period", "visualize", start_offset_ms=100),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["visualize"]["calls"], 1)
        self.assertEqual(by_name["visualize"]["derived"], True)

    def test_derived_badge_stays_off_when_native_period_merges(self):
        traces = [
            _trace(
                "t1",
                [
                    _native_skill_span("s1", "root", "visualize"),
                    _skill_span("s2", "s1", "visualize"),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["visualize"]["calls"], 1)
        self.assertEqual(by_name["visualize"]["derived"], False)

    def test_relation_edges_ignore_the_usage_merge(self):
        traces = [
            _trace(
                "t1",
                [
                    _native_skill_span("outer", "root", "visualize"),
                    _skill_span("inner", "outer", "blog-post"),
                ],
            )
        ]
        result = _scan_skill_window(traces, "codex")
        by_name = {item["name"]: item for item in result["skills"]}
        self.assertEqual(by_name["visualize"]["calls"], 1)
        self.assertEqual(by_name["blog-post"]["calls"], 1)
        self.assertEqual(len(result["relations"]), 1)
        self.assertEqual(result["relations"][0]["from"], "visualize")
        self.assertEqual(result["relations"][0]["to"], "blog-post")


class SanitizeIncomingTests(SimpleTestCase):
    def test_rejects_missing_name_and_oversized_fields(self):
        self.assertIsNone(_sanitize_incoming({"description": "x"}))
        self.assertIsNone(_sanitize_incoming({"name": "x" * 200}))
        # Agent Skills caps descriptions at 1024 chars; TypeSafe-style
        # folded SKILL.md prose runs ~660 and must be accepted.
        self.assertIsNone(
            _sanitize_incoming({"name": "x", "description": "y" * 1100})
        )
        self.assertEqual(
            _sanitize_incoming({"name": "x", "description": "y" * 660})[
                "description"
            ],
            "y" * 660,
        )
        self.assertIsNone(_sanitize_incoming({"name": "x", "raw": "text"}))
        self.assertIsNone(_sanitize_incoming({"name": "x", "allowedTools": [1]}))

    def test_normalizes_valid_payload(self):
        parsed = _sanitize_incoming(
            {
                "name": " blog-post ",
                "description": "write posts",
                "path": "/skills/blog-post",
                "source": "skillmd",
                "raw": {"content": "---\nname: blog-post\n---"},
                "allowedTools": ["read_file", " ", "write_file"],
                "license": "MIT",
                "compatibility": ">=0.1",
            }
        )
        self.assertEqual(parsed["name"], "blog-post")
        self.assertEqual(parsed["allowedTools"], ["read_file", "write_file"])
        self.assertEqual(parsed["raw"]["content"].startswith("---"), True)


class SkillConfigApiTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("ops@example.com")
        self.space = Space.objects.create(
            name="labs-skill", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_config_summary_keeps_metadata_without_loading_markdown(self):
        base = f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs"
        content = "---\nname: docs\n---\n" + "Example content. " * 2000
        SkillConfig.objects.create(
            space=self.space,
            name="docs",
            description="Read the docs",
            path="src/SKILL.md",
            raw={"content": content},
        )
        legacy = self.client.get(base)
        self.assertEqual(legacy.status_code, 200)
        [full] = legacy.json()["data"]
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(base, {"view": "summary"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            [{key: value for key, value in full.items() if key != "raw"}],
        )
        config_queries = [
            entry["sql"]
            for entry in queries
            if SkillConfig._meta.db_table in entry["sql"]
        ]
        self.assertEqual(len(config_queries), 1, config_queries)
        self.assertNotRegex(config_queries[0], r"\braw\b")
        self.assertEqual(self.client.get(base, {"view": "detail", "name": "docs"}).json()["data"], full)

    def test_config_detail_respects_read_scope_and_soft_delete(self):
        config = SkillConfig.objects.create(space=self.space, name="..")
        base = f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs"
        params = {"view": "detail", "name": config.name}
        reader = create_local_user("read-detail")
        self.client.force_authenticate(user=reader)
        denied = self.client.get(base, params)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()["code"], 3002)
        create_test_space_member(space=self.space, user=reader, role=3)
        self.assertEqual(self.client.get(base, params).json()["data"]["name"], "..")
        config.gmt_deleted = timezone.now()
        config.save(update_fields=["gmt_deleted"])
        response = self.client.get(base, params)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 3104)
        self.space.status = 0
        self.space.save(update_fields=["status"])
        self.assertEqual(self.client.get(base, params).json()["code"], 3001)

    def test_observation_summary_preserves_overview_and_history(self):
        url = f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations"
        params = {
            "fromTimestamp": "2026-01-01T00:00:00Z",
            "toTimestamp": "2026-01-02T00:00:00Z",
        }
        spans = [
            _native_skill_span("parent", "root", "docs"),
            _native_skill_span("child", "parent", "report"),
        ]

        def get(**options):
            response = self.client.get(url, {**params, **options})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["code"], 0)
            return response.json()["data"]

        with patch(
            "apps.core.views.labs_skill.get_default_trace_query_service"
        ) as factory:
            factory.return_value.list_skill_spans.return_value = {
                "spans": spans,
                "spanMeta": {"totalItems": len(spans), "totalPages": 1},
            }
            legacy = get()
            summary = get(view="summary")
            history = get(history_skill="docs")
            projected_history = get(view="summary", history_skill="docs")

        self.assertTrue(legacy["relations"])
        self.assertEqual(
            summary,
            {
                "overview": legacy["overview"],
                "skills": [
                    {key: value for key, value in row.items() if key != "recentCalls"}
                    for row in legacy["skills"]
                ],
            },
        )
        self.assertEqual(projected_history, history)

    def test_config_roundtrip(self):
        payload = {
            "skills": [
                {
                    "name": "blog-post",
                    "description": "write posts",
                    "raw": {"content": "---\nname: blog-post\n---"},
                }
            ]
        }
        response = self.client.put(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["name"], "blog-post")

        listed = self.client.get(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["data"]), 1)

        deleted = self.client.delete(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs/blog-post"
        )
        self.assertEqual(deleted.status_code, 200)
        listed = self.client.get(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs"
        )
        self.assertEqual(listed.json()["data"], [])

    def test_import_receipts_include_only_the_written_names(self):
        url = f"/api/v2/spaces/{self.space.uuid}/labs/skill/configs"
        SkillConfig.objects.create(space=self.space, name="unrelated")
        SkillConfig.objects.create(space=self.space, name="Docs")
        # OceanBase uses a case-insensitive collation, while SQLite may insert
        # a second row. The receipt always names the row this request wrote.
        stored_name = (
            "Docs"
            if SkillConfig.objects.filter(space=self.space, name="docs").exists()
            else "docs"
        )
        content = "---\nname: docs\n---\n" + "Example content. " * 2000
        response = self.client.put(
            f"{url}?response=receipts",
            {"skills": [{"name": "docs", "raw": {"content": content}}]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(
            response.data["data"], [{"requestedName": "docs", "name": stored_name}]
        )
        listed = self.client.get(url)
        skills = {entry["name"]: entry for entry in listed.data["data"]}
        self.assertIn("unrelated", skills)
        self.assertEqual(skills[stored_name]["raw"]["content"], content)

    def test_history_queries_records_beyond_recent_preview_and_filters_before_paging(self):
        spans = [_skill_span(f"span-{index}", "root", "deploy", index) for index in range(205)]
        spans[0]["status"] = {"code": "ERROR", "message": "old failure"}
        scan = {"spans": spans, "spanMeta": {"totalItems": 205, "totalPages": 1, "page": 1, "limit": 500}}
        with patch("apps.core.views.labs_skill.get_default_trace_query_service") as factory:
            factory.return_value.list_skill_spans.return_value = scan
            response = self.client.get(f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations", {"fromTimestamp":"2026-01-01T00:00:00Z", "toTimestamp":"2026-01-02T00:00:00Z", "history_skill":"deploy", "page":"3", "limit":"100"})
        self.assertEqual(response.status_code, 200)
        result = response.json()["data"]
        self.assertEqual(result["total"], 205)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["calls"]), 5)
        self.assertEqual(result["calls"][-1]["spanId"], "span-0")
        with patch("apps.core.views.labs_skill.get_default_trace_query_service") as factory:
            factory.return_value.list_skill_spans.return_value = scan
            response = self.client.get(f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations", {"fromTimestamp":"2026-01-01T00:00:00Z", "toTimestamp":"2026-01-02T00:00:00Z", "history_skill":"deploy", "status":"error", "limit":"5"})
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["calls"][0]["spanId"], "span-0")

    def test_history_orders_fractional_times_and_missing_measurements_last(self):
        spans = [_native_skill_span(name, "root", "deploy") for name in ["whole", "fractional", "unknown"]]
        spans[0]["start_time_unix_nano"] = "2026-10-27T00:00:00Z"
        spans[1]["start_time_unix_nano"] = "2026-10-27T00:00:00.500000Z"
        for span in spans[:2]:
            span["attributes"]["agentic.tool.duration_ms"] = 1
        spans[2].pop("start_time_unix_nano")
        spans[2].pop("end_time_unix_nano")
        scan = {"spans":spans,"spanMeta":{"totalItems":3,"totalPages":1,"page":1,"limit":500}}
        for order, expected in [("recent",["fractional","whole","unknown"]),("oldest",["whole","fractional","unknown"]),("duration_desc",["fractional","whole","unknown"])]:
            with self.subTest(order=order), patch("apps.core.views.labs_skill.get_default_trace_query_service") as factory:
                factory.return_value.list_skill_spans.return_value = scan
                response = self.client.get(f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations", {"fromTimestamp":"2026-10-27T00:00:00Z","toTimestamp":"2026-10-28T00:00:00Z","history_skill":"deploy","sort":order})
                self.assertEqual([call["spanId"] for call in response.json()["data"]["calls"]],expected)

    def test_observations_require_window(self):
        response = self.client.get(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations"
        )
        self.assertEqual(response.status_code, 400)

    def test_observation_source_failures_remain_errors_for_overview_and_history(self):
        for history in (False, True):
            with self.subTest(history=history), patch(
                "apps.core.views.labs_skill.get_default_trace_query_service"
            ) as factory:
                factory.return_value.list_skill_spans.side_effect = TraceQueryServiceError(
                    "SigNoz endpoint is not configured", 3011
                )
                params = {
                    "fromTimestamp": "2026-01-01T00:00:00Z",
                    "toTimestamp": "2026-01-02T00:00:00Z",
                }
                if history:
                    params["history_skill"] = "deploy"
                response = self.client.get(
                    f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations", params
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], 3011)
                self.assertEqual(response.json()["message"], "SigNoz endpoint is not configured")
                self.assertNotIn("complete", response.json().get("data") or {})

    def test_unsupported_langfuse_observations_are_not_verified_empty_windows(self):
        service = TraceQueryService()
        service.register_provider(
            "langfuse", LangfuseTraceProvider(), source_systems=("langfuse",)
        )
        for history in (False, True):
            with self.subTest(history=history):
                params = {
                    "fromTimestamp": "2026-01-01T00:00:00Z",
                    "toTimestamp": "2026-01-02T00:00:00Z",
                }
                if history:
                    params["history_skill"] = "deploy"
                with patch(
                    "apps.core.views.labs_skill.get_default_trace_query_service",
                    return_value=service,
                ), patch(
                    "apps.core.trace_query.service.resolve_trace_source_system",
                    return_value="langfuse",
                ), patch("requests.get") as get, patch("requests.post") as post:
                    response = self.client.get(
                        f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations",
                        params,
                    )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 3011)
                self.assertIn("not supported", response.data["message"])
                self.assertNotIn("complete", response.data.get("data") or {})
                get.assert_not_called()
                post.assert_not_called()

    def test_verified_empty_windows_still_succeed_for_overview_and_history(self):
        for history in (False, True):
            for pages in (0, 1):
                with self.subTest(history=history, pages=pages), patch(
                    "apps.core.views.labs_skill.get_default_trace_query_service"
                ) as factory:
                    factory.return_value.list_skill_spans.return_value = {
                        "spans": [],
                        "spanMeta": {"totalItems": 0, "totalPages": pages},
                    }
                    params = {
                        "fromTimestamp": "2026-01-01T00:00:00Z",
                        "toTimestamp": "2026-01-02T00:00:00Z",
                    }
                    if history:
                        params["history_skill"] = "deploy"
                    response = self.client.get(
                        f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations", params
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["code"], 0)
                    result = response.json()["data"]
                    if history:
                        self.assertEqual(result["calls"], [])
                        self.assertEqual(result["total"], 0)
                        self.assertTrue(result["complete"])
                    else:
                        self.assertEqual(result["skills"], [])
                        self.assertEqual(result["overview"]["calls"], 0)
                        self.assertEqual(result["overview"]["availableTraces"], 0)
                        self.assertIsNone(result["overview"]["p95Ms"])

    def test_observations_aggregate_from_window(self):
        scan = {
            "spans": [_skill_span("s1", "root", "blog-post")],
            "spanMeta": {"totalItems": 1, "totalPages": 1},
        }
        with patch(
            "apps.core.views.labs_skill.get_default_trace_query_service"
        ) as factory:
            factory.return_value.list_skill_spans.return_value = scan
            response = self.client.get(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations",
                {"fromTimestamp": "2026-01-01T00:00:00Z", "toTimestamp": "2026-01-02T00:00:00Z"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["overview"]["skillCount"], 1)
        self.assertEqual(body["skills"][0]["name"], "blog-post")

    def test_observations_aggregate_every_storage_page(self):
        pages = [
            {
                "spans": [
                    _skill_span(f"s{i}", "root", "blog-post") for i in range(500)
                ],
                "spanMeta": {
                    "totalItems": 501,
                    "totalPages": 2,
                    "page": 1,
                    "limit": 500,
                },
            },
            {
                "spans": [_skill_span("last", "root", "visualize")],
                "spanMeta": {
                    "totalItems": 501,
                    "totalPages": 2,
                    "page": 2,
                    "limit": 500,
                },
            },
        ]
        with patch(
            "apps.core.views.labs_skill.get_default_trace_query_service"
        ) as factory:
            factory.return_value.list_skill_spans.side_effect = pages
            response = self.client.get(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations",
                {
                    "fromTimestamp": "2026-01-01T00:00:00Z",
                    "toTimestamp": "2026-01-02T00:00:00Z",
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertEqual(body["overview"]["calls"], 501)
        self.assertEqual(
            {item["name"] for item in body["skills"]}, {"blog-post", "visualize"}
        )

    def test_observations_refuse_an_incomplete_storage_page(self):
        scan = {
            "spans": [_skill_span("only", "root", "blog-post")],
            "spanMeta": {"totalItems": 501, "totalPages": 2, "page": 1, "limit": 500},
        }
        with patch(
            "apps.core.views.labs_skill.get_default_trace_query_service"
        ) as factory:
            factory.return_value.list_skill_spans.return_value = scan
            response = self.client.get(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/observations",
                {
                    "fromTimestamp": "2026-01-01T00:00:00Z",
                    "toTimestamp": "2026-01-02T00:00:00Z",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], 3035)
