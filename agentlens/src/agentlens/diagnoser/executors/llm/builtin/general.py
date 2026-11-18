"""Finite sourced-material gates for the four saved Core variants."""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from dataclasses import asdict
from typing import Any

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.definitions.catalog import validate_core_spec
from agentlens.diagnoser.executors.llm.base import LlmMetricExecutor
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.service.datasource.projection.models import SourceReference, TextEvidence
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection

_UNITTEST_FLAGS = {
    "-v",
    "-q",
    "-f",
    "-b",
    "-c",
    "--verbose",
    "--quiet",
    "--failfast",
    "--buffer",
    "--catch",
}
_PYTHON_FILE_PATH = r"[A-Za-z0-9_./-]+\.py"
# Quoted regions, including unfinished ones, stay in one complete token.
# Never scan an unknown literal again for a partial filename.
_FINAL_TOKENS = re.compile(r"""(?:`[^`]*(?:`|$)|"[^"]*(?:"|$)|'[^']*(?:'|$)|[^\s`"'])+""")


def _final_file_targets(final: str) -> set[str]:
    targets = set()
    for token in _FINAL_TOKENS.findall(final):
        if token.startswith("(") and token.endswith(")"):
            token = token[1:-1]
        if not token:
            continue
        if token[0] in "`\"'":
            end = token.find(token[0], 1)
            if end < 0 or token[end + 1 :] not in {"", ".", ",", ";", ":", "!", "?"}:
                continue
            token = token[1:end]
        if re.fullmatch(_PYTHON_FILE_PATH, token) is not None:
            targets.add(token)
    return targets


def _unittest_file_target(command: str) -> str | None:
    """Read one literal file operand; never resolve paths or interpret a shell."""
    if any(char in command for char in "\n\r\x00;&|<>$`\\{}*?[]~()"):
        return None
    try:
        args = shlex.split(command)
    except ValueError:
        return None
    if len(args) < 4 or args[0] not in {"python", "python3"} or args[1:3] != ["-m", "unittest"]:
        return None
    files = []
    for arg in args[3:]:
        if arg in _UNITTEST_FLAGS:
            continue
        if arg.startswith("-") or re.fullmatch(_PYTHON_FILE_PATH, arg) is None:
            return None
        files.append(arg)
    return files[0] if len(files) == 1 else None


@ExecutorRegistry.register("llm", "general_llm")
class GeneralLlmExecutor(LlmMetricExecutor):
    def _validate(self) -> None:
        try:
            validate_core_spec(self.spec)
        except (ValueError, TypeError) as exc:
            raise ExecutorFailure("invalid_spec", str(exc)) from exc

    def _source_valid(self, source: SourceReference, trace) -> bool:
        if not isinstance(source, SourceReference) or not source.span_id or not source.field_path:
            return False
        for row in trace.spans:
            if source.span_id != str(row.get("span_id") or row.get("spanID") or ""):
                continue
            if source.trace_id not in {trace.trace_id, str(row.get("trace_id") or "")}:
                continue
            # Reference original recorded fields (flat attributes or an explicit
            # nested carrier), never synthetic "tool_action"/"file_mutation" paths.
            return any(
                source.field_path == key or source.field_path.startswith((key + ".", key + "["))
                for key in row
                if isinstance(key, str)
            )
        return False

    def _action_sources(self, trace, action, *, status_required=False) -> list[dict]:
        required = ("arguments", "result", "status") if status_required else ("arguments", "result")
        refs = [action.field_sources.get(key) for key in required]
        if not all(self._source_valid(ref, trace) for ref in refs):
            return []
        return [asdict(ref) for ref in refs]

    def _text(self, value: TextEvidence | None, trace) -> dict | None:
        if value is None or not isinstance(value.content, str) or not value.content.strip():
            return None
        if not value.sources or not all(self._source_valid(s, trace) for s in value.sources):
            return None
        result = {"content": value.content, "sources": [asdict(s) for s in value.sources]}
        producer = getattr(value, "producer_source", None)
        if producer is not None and self._source_valid(producer, trace):
            result["producer_source"] = asdict(producer)
        return result

    def _facts(self, trace, projection) -> tuple[list[dict], list[dict]]:
        artifacts, observed = [], []
        if not isinstance(projection, CodingProjection):
            return artifacts, observed
        for action in projection.tool_actions:
            sources = self._action_sources(trace, action)
            if not sources or action.input_truncated is True or action.output_truncated is True:
                continue
            args = action.arguments if isinstance(action.arguments, dict) else {}
            path = args.get("file_path") or args.get("path")
            content = args.get("content")
            # An explicit successful write with target/content/result is observed;
            # status success without these facts is never an artifact.
            if (
                action.name in {"write_file", "write", "Write", "fs_write_file"}
                and isinstance(path, str)
                and path.strip()
                and content is not None
                and action.status == "success"
                and action.result not in (None, "", {}, [])
            ):
                write_sources = self._action_sources(trace, action, status_required=True)
                if write_sources:
                    artifacts.append(
                        {
                            "target": path,
                            "content": content,
                            "result": action.result,
                            "sources": write_sources,
                        }
                    )
            command = args.get("command") or args.get("cmd")
            if (
                isinstance(command, str)
                and command.strip()
                and action.result not in (None, "", {}, [])
            ):
                fact = {
                    "target": command,
                    "content": json.dumps(action.result, ensure_ascii=False),
                    "status": action.status,
                    "call_id": action.call_id,
                    "sources": sources,
                }
                observed.append(fact)
        by_action = {a.action_id: a for a in projection.tool_actions}
        for mutation in projection.file_mutations:
            action = by_action.get(mutation.source_action_id)
            if (
                action is None
                or action.status != "success"
                or mutation.evidence_level == "inferred"
                or action.input_truncated is True
                or action.output_truncated is True
                or action.result in (None, "", {}, [])
            ):
                continue
            sources = self._action_sources(trace, action, status_required=True)
            if sources and mutation.source_span_id == action.source_span_id:
                artifacts.append(
                    {
                        "target": mutation.path,
                        "content": mutation.after,
                        "result": action.result,
                        "sources": sources,
                    }
                )
        for check in projection.verification_actions:
            action = by_action.get(check.source_action_id)
            sources = self._action_sources(trace, action) if action is not None else []
            if (
                check.command
                and check.result != "unknown"
                and sources
                and action is not None
                and action.input_truncated is not True
                and action.output_truncated is not True
            ):
                artifacts.append(
                    {"target": check.command, "content": check.result, "sources": sources}
                )
        return artifacts, observed

    def _bundle(self, trace, projection) -> dict[str, Any]:
        self._validate()
        bundle = {}
        for key, value in (
            ("request", projection.current_request),
            ("final", projection.final_output),
        ):
            item = self._text(value, trace)
            if item is not None:
                bundle[key] = item
        variant = self.spec["canonical_variant"]
        artifacts, observed = (
            self._facts(trace, projection)
            if variant in {"executed_artifact", "observed_fact_comparison"}
            else ([], [])
        )
        if variant == "executed_artifact":
            bundle.update({f"artifact:{i}": item for i, item in enumerate(artifacts)})
        elif variant == "observed_fact_comparison":
            final = bundle.get("final", {}).get("content", "")
            # Explicit correspondence only. No keyword-based intent classifier or
            # assumption that missing instrumentation proves non-execution.
            paths = [_unittest_file_target(item["target"]) for item in observed]
            path_counts: Counter[str] = Counter()
            if isinstance(projection, CodingProjection):
                for action in projection.tool_actions:
                    args = action.arguments if isinstance(action.arguments, dict) else {}
                    command = args.get("command") or args.get("cmd")
                    path = _unittest_file_target(command) if isinstance(command, str) else None
                    if path is not None:
                        # A dropped/truncated event can make correspondence
                        # ambiguous, but cannot supply evidence or a result.
                        path_counts[path] += 1
            final_paths = _final_file_targets(final)
            matched = [
                item
                for item, path in zip(observed, paths, strict=True)
                if item["target"] in final
                or (item.get("call_id") and item["call_id"] in final)
                # Alias only chooses an existing fact. Repeated events require
                # the original command/call correspondence, never last/all.
                or (path is not None and path_counts[path] == 1 and path in final_paths)
            ]
            bundle.update({f"observed:{i}": item for i, item in enumerate(matched)})
        elif variant == "provided_context":
            contexts = [
                self._text(item, trace)
                for item in projection.provided_context or []
                if item.kind not in {"instruction", "system", "developer"}
            ]
            bundle.update(
                {f"context:{i}": item for i, item in enumerate(contexts) if item is not None}
            )
        return bundle

    def applicability_skip_reason(self, trace):
        self._validate()
        projection = trace.require_projection()
        bundle = self._bundle(trace, projection)
        if any(
            "[REDACTED]" in str(getattr(v, "content", ""))
            for v in (projection.current_request, projection.final_output)
        ):
            return "privacy_redacted: required current/final material is redacted"
        if self.spec["canonical_variant"] == "provided_context" and any(
            "[REDACTED]" in str(v.content) for v in projection.provided_context or []
        ):
            return "privacy_redacted: required provided context is redacted"
        if "request" not in bundle or "final" not in bundle:
            return "missing_required_evidence: sourced current request and final output"
        prefix = {
            "executed_artifact": "artifact:",
            "observed_fact_comparison": "observed:",
            "provided_context": "context:",
        }.get(self.spec["canonical_variant"])
        required_facts = [item for key, item in bundle.items() if prefix and key.startswith(prefix)]
        if prefix == "artifact:" and isinstance(projection, CodingProjection):
            # A redacted verification result can project as "unknown" and thus
            # disappear from the artifact bundle. Inspect its sourced payload
            # before treating that absence as ordinary missing instrumentation.
            by_action = {action.action_id: action for action in projection.tool_actions}
            for check in projection.verification_actions:
                action = by_action.get(check.source_action_id)
                if (
                    check.command
                    and action is not None
                    and action.input_truncated is not True
                    and action.output_truncated is not True
                    and self._action_sources(trace, action)
                ):
                    required_facts.append({"target": check.command, "result": action.result})
        if any(
            "[REDACTED]" in str(item.get(field, ""))
            for item in required_facts
            for field in ("content", "target", "result")
        ):
            # Do not let another visible fact stand in for an obscured dependency.
            # Scope is the selected Core's facts, never unrelated tool metadata.
            return "privacy_redacted: required fact material is redacted"
        if prefix and not any(key.startswith(prefix) for key in bundle):
            return "missing_required_evidence: " + self.spec["canonical_variant"]
        return None

    def _extra_prompt_inputs(self, trace, projection):
        return {"evidence_bundle": self._bundle(trace, projection)}

    def evaluate(self, trace):
        result = super().evaluate(trace)
        bundle = self._bundle(trace, trace.require_projection())
        variant = self.spec["canonical_variant"]
        prefix = {
            "executed_artifact": "artifact:",
            "observed_fact_comparison": "observed:",
            "provided_context": "context:",
        }.get(variant)
        for metric in result.metrics:
            if not isinstance(metric.reason, str) or not metric.reason.strip():
                raise ExecutorFailure("invalid_result", "Core semantic reason is required")
            citations = metric.evidence.get("citations")
            if not isinstance(citations, list) or not citations:
                raise ExecutorFailure("invalid_result", "Core citation evidence is required")
            cited = set()
            for citation in citations:
                if not isinstance(citation, dict):
                    raise ExecutorFailure("invalid_result", "invalid Core citation")
                key, quote = citation.get("source_id"), citation.get("quote")
                if (
                    not isinstance(key, str)
                    or key not in bundle
                    or not isinstance(quote, str)
                    or not quote.strip()
                    or quote not in str(bundle[key].get("content", ""))
                    and quote not in str(bundle[key].get("target", ""))
                    and quote not in json.dumps(bundle[key].get("result", ""), ensure_ascii=False)
                ):
                    raise ExecutorFailure("invalid_result", "unlocatable Core citation")
                cited.add(key)
            if not {"request", "final"}.issubset(cited) or (
                prefix and not any(k.startswith(prefix) for k in cited)
            ):
                raise ExecutorFailure("invalid_result", "Core citation coverage is incomplete")
            metric.evidence["evaluation_scope"] = variant
            if variant == "text_response":
                metric.reason = "只核对文本要求；外部执行未在此项验证。" + metric.reason
            elif variant == "observed_fact_comparison" and metric.verdict.value == "good":
                metric.reason = "已核对的执行声明未发现矛盾。" + metric.reason
        return result
