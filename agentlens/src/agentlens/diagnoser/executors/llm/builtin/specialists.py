"""Independent sourced semantic specialists; no retired/Core prompt variables."""

from __future__ import annotations

from agentlens.diagnoser.base import ExecutorFailure, parse_user_metrics
from agentlens.diagnoser.definitions.specialists import validate_specialist_spec
from agentlens.diagnoser.executors.llm.base import LlmMetricExecutor
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.service.datasource.specialist_materials import build_specialist_materials


@ExecutorRegistry.register("llm", "specialist_llm")
class SpecialistLlmExecutor(LlmMetricExecutor):
    def _validate(self):
        try:
            validate_specialist_spec(self.spec)
        except (TypeError, ValueError):
            raise ExecutorFailure("invalid_spec", "专项配置或 canonical 身份无效") from None

    def applicability_skip_reason(self, trace):
        self._validate()
        build_specialist_materials(trace, self.spec)
        return None

    def _extra_prompt_inputs(self, trace, projection):
        self._validate()
        return {"evidence_bundle": build_specialist_materials(trace, self.spec)}

    def _parse_judge_output(self, output, definitions):
        metrics = parse_user_metrics(output, [self.spec["builtin_key"]])
        if len(metrics) != 1:
            raise ExecutorFailure("invalid_result", "专项必须返回一个 canonical metric")
        return metrics

    def evaluate(self, trace):
        self._validate()
        bundle = build_specialist_materials(trace, self.spec)
        result = super().evaluate(trace)
        metric = result.metrics[0]
        citations = metric.evidence.get("citations")
        if (
            not metric.reason.strip()
            or len(metric.reason) > 2048
            or not isinstance(citations, list)
            or not 1 <= len(citations) <= 32
        ):
            raise ExecutorFailure("invalid_result", "专项 reason 或引用边界无效")
        cited = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "quote"}:
                raise ExecutorFailure("invalid_result", "专项引用结构无效")
            source_id, quote = citation["source_id"], citation["quote"]
            if (
                not isinstance(source_id, str)
                or source_id not in bundle
                or not isinstance(quote, str)
                or not quote.strip()
                or len(quote) > 512
            ):
                raise ExecutorFailure("invalid_result", "专项引用来源或大小无效")
            item = bundle[source_id]
            if "sources" in item:
                from agentlens.service.datasource.projection.models import SourceReference
                from agentlens.service.datasource.specialist_materials import resolve_source_leaf

                locatable = any(
                    quote in resolve_source_leaf(trace, SourceReference(**ref)).text
                    for ref in item["sources"]
                )
            else:
                locatable = quote in item["content"]
            if not locatable:
                raise ExecutorFailure("invalid_result", "专项引用无法定位到实际 leaf")
            cited.add(source_id)
        if (
            not {"request", "final"}.issubset(cited)
            or (
                self.spec["canonical_variant"] == "recorded_conversation"
                and not any(key.startswith("history:") for key in cited)
            )
            or (self.spec["builtin_key"] == "response_language" and "policy:language" not in cited)
        ):
            raise ExecutorFailure("invalid_result", "专项引用未覆盖必要材料")
        metric.evidence["evaluation_scope"] = self.spec["canonical_variant"]
        metric.evidence["result_boundary"] = self.spec["result_boundary"]
        metric.reason = self.spec["result_boundary"] + metric.reason
        return result
