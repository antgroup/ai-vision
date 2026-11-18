"""Trace detail projection must share the Runner projection contract."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI

from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import DatasetType
from agentlens.web.routes import trace as trace_route


def test_trace_projection_does_not_declare_resource_scope_query_parameters():
    app = FastAPI()
    app.include_router(trace_route.router)
    parameters = app.openapi()["paths"]["/api/v1/trace/{trace_id}/projection"]["get"]["parameters"]
    parameter_names = {parameter["name"] for parameter in parameters}

    assert "space_uuid" not in parameter_names
    assert "agent_uuid" not in parameter_names


class TestProjection:
    class _TraceStore:
        def __init__(self, rows: list[dict]) -> None:
            self.rows = rows
            self.specs = []

        def read_trace_spans(
            self, spec, trace_id: str, _center_time: str, *, window_min: int
        ) -> list[dict]:
            assert trace_id == "trace-1"
            assert window_min == 24 * 60
            self.specs.append(spec)
            return self.rows

    def test_signoz_detail_uses_raw_row_projector_and_preserves_runtime(self):
        trace_route._project_signoz.cache_clear()
        rows = [
            {
                "span_id": "turn-1",
                "parent_span_id": "",
                "trace_id": "trace-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "start_time_unix_nano": "2026-07-23T14:21:35.976+08:00",
                "end_time_unix_nano": "2026-07-23T14:22:35.976+08:00",
                "status_code": 1,
                "attributes.gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "hello"}]
                ),
                "attributes.gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "hi"}]
                ),
            }
        ]
        result = trace_route._project_signoz(
            self._TraceStore(rows),
            "trace-1",
            "aicoder.aivision_trace",
            "",
            "2026-07-23T14:21:35.976+08:00",
            "codex",
        )
        assert result is not None
        assert result["runtime_type"] == "codex"
        assert result["user_query"] == "hello"
        assert result["agent_output"] == "hi"

    def test_tool_only_coding_trace_remains_available_for_step_replay(self):
        trace_route._project_signoz.cache_clear()
        rows = [
            {
                "span_id": "tool-1",
                "parent_span_id": "",
                "trace_id": "trace-1",
                "name": "tool Edit",
                "kind": "SPAN_KIND_INTERNAL",
                "status_code": 1,
                "attributes.gen_ai.span.kind": "TOOL",
                "attributes.gen_ai.tool.call.id": "edit-call-1",
                "attributes.gen_ai.tool.call.arguments": json.dumps(
                    {
                        "path": "/repo/example.py",
                        "old_string": "before",
                        "new_string": "after",
                    }
                ),
            },
        ]
        result = trace_route._project_signoz(
            self._TraceStore(rows),
            "trace-1",
            "aicoder.aivision_trace",
            "",
            "2026-07-23T14:21:35.976+08:00",
            "codex",
        )

        assert result is not None
        assert [step["node"] for step in result["steps"]] == ["tool Edit"]

    def test_multiple_chat_spans_fall_back_to_raw_detail(self):
        trace_route._project_signoz.cache_clear()
        rows = [
            {
                "span_id": "turn-1",
                "parent_span_id": "",
                "trace_id": "trace-1",
                "name": "Turn 1",
                "kind": "SPAN_KIND_SERVER",
                "status_code": 1,
                "attributes.gen_ai.span.kind": "CHAT",
                "attributes.gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "first"}]
                ),
                "attributes.gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "first answer"}]
                ),
            },
            {
                "span_id": "turn-2",
                "parent_span_id": "",
                "trace_id": "trace-1",
                "name": "Turn 2",
                "kind": "SPAN_KIND_SERVER",
                "status_code": 1,
                "attributes.gen_ai.span.kind": "CHAT",
                "attributes.gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "second"}]
                ),
                "attributes.gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "second answer"}]
                ),
            },
            {
                "span_id": "edit-2",
                "parent_span_id": "turn-2",
                "trace_id": "trace-1",
                "name": "tool edit",
                "kind": "SPAN_KIND_INTERNAL",
                "status_code": 1,
                "attributes.gen_ai.span.kind": "TOOL",
                "attributes.gen_ai.tool.call.id": "edit-call-2",
                "attributes.gen_ai.tool.call.arguments": json.dumps(
                    {
                        "path": "/repo/example.py",
                        "old_string": "before",
                        "new_string": "after",
                    }
                ),
            },
        ]
        result = trace_route._project_signoz(
            self._TraceStore(rows),
            "trace-1",
            "aicoder.aivision_trace",
            "",
            "2026-07-23T14:21:35.976+08:00",
            "codex",
        )

        assert result is not None
        assert result["projection_kind"] == "raw"
        assert result["raw"] == {"spans": rows}
        assert "timestamp" not in result

    def test_signoz_detail_uses_dataset_source_scope_only(self):
        dataset = Dataset(
            uuid="dataset-1",
            space_uuid="space-1",
            agent_uuid="agent-1",
            name="signoz",
            description="",
            type=DatasetType.SIGNOZ,
            spec={
                "runtime_type": "other",
                "username_column": "attributes.user",
                "username": "u1",
                "agent_product_column": "resource.product",
                "agent_product_filter": "Coding",
                "query": {"table": "trace", "where": "name = 'candidate'"},
            },
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
        trace_store = self._TraceStore(
            [{"trace_id": "trace-1", "span_id": "span-1", "name": "raw span"}]
        )
        jobs = SimpleNamespace(
            get_by_uuid=lambda uuid: SimpleNamespace(uuid=uuid, dataset_uuid="dataset-1")
        )
        datasets = SimpleNamespace(
            get_by_uuid=lambda uuid: dataset if uuid == "dataset-1" else None
        )

        result = trace_route.trace_projection(
            "trace-1",
            traces=trace_store,
            jobs=jobs,
            datasets=datasets,
            job_uuid="job-1",
            center_time="2026-07-30T20:15:00",
        )

        assert result["projection_kind"] == "raw"
        assert trace_store.specs[0].where == (
            "`attributes.user` = 'u1' AND `resource.product` = 'Coding'"
        )
        assert "candidate" not in trace_store.specs[0].where
