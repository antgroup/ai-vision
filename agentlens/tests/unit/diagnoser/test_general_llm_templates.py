"""Core protocol/gates; fixed labels below are human-authored oracles, not Judge accuracy."""

import importlib
import importlib.util
import json
from dataclasses import replace

import pytest

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    ExecutorFailure,
    execute_with_applicability,
)
from agentlens.service.datasource.projection.models import (
    ContextEvidence,
    Projection,
    SourceReference,
    TextEvidence,
    TraceOutcome,
)
from agentlens.service.datasource.projection.projectors.coding.models import (
    CodingProjection,
    ToolAction,
)
from agentlens.service.datasource.trace import Trace


def catalog():
    name = "agentlens.diagnoser.definitions.catalog"
    try:
        found = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        found = None
    assert found is not None, "canonical Core catalog is missing"
    return importlib.import_module(name)


def core(key="task_completion", variant="text_response"):
    spec = catalog().get_core_definition(key, variant)["spec"]
    from agentlens.diagnoser.executors.llm.builtin.general import GeneralLlmExecutor

    return GeneralLlmExecutor(spec)


def trace(*, coding=False, request="给我两列CSV", final="name,value\na,1", actions=()):
    ref = SourceReference("t", "s", "attributes.input")
    values = dict(
        timestamp=None,
        user_query=request,
        agent_output=final,
        outcome=TraceOutcome(False, None, 10, False),
        labels={},
        current_request=TextEvidence(request, [ref]),
        final_output=TextEvidence(final, [replace(ref, field_path="attributes.output")]),
    )
    p = CodingProjection(**values, tool_actions=list(actions)) if coding else Projection(**values)
    return Trace(
        "t",
        "my-runtime",
        [
            {
                "span_id": "s",
                "attributes.input": request,
                "attributes.output": final,
                "attributes.gen_ai.tool.call.arguments": "fixture",
                "attributes.gen_ai.tool.call.result": "fixture",
                "status_code": 1,
            }
        ],
        projection=p,
    )


def sourced_action(*args, **kwargs):
    kwargs["field_sources"] = {
        key: SourceReference("t", "s", path)
        for key, path in {
            "arguments": "attributes.gen_ai.tool.call.arguments",
            "result": "attributes.gen_ai.tool.call.result",
            "status": "status_code",
        }.items()
    }
    return ToolAction(*args, **kwargs)


def test_catalog_distinguishes_four_identities_and_returns_independent_specs():
    c = catalog()
    definitions = c.load_core_definitions()
    assert {(d["key"], d["variant"]) for d in definitions} == {
        ("task_completion", "text_response"),
        ("task_completion", "executed_artifact"),
        ("report_fidelity", "observed_fact_comparison"),
        ("answer_grounding", "provided_context"),
    }
    definitions[0]["spec"]["prompt"] = "changed"
    assert c.load_core_definitions()[0]["spec"]["prompt"] != "changed"
    assert all(d["mode"] == "llm" and d["spec"]["score_role"] == "quality" for d in definitions)


def test_scene_presets_do_not_pollute_qa_or_runtime_with_coding_quality():
    presets = {p["scene_key"]: p for p in catalog().load_scene_presets()}
    assert "test_execution" not in presets["conversation_qa"]["rule_keys"]
    assert "test_execution" not in presets["quick_runtime"]["rule_keys"]
    assert "test_execution" in presets["coding_result"]["rule_keys"]
    assert presets["quick_runtime"]["default_core"] is None
    with pytest.raises(ValueError):
        catalog().resolve_scene_core(None, "task_completion")
    with pytest.raises(ValueError):
        catalog().resolve_scene_core("quick_runtime", "task_completion")


def test_catalog_exports_explicit_rule_identity_and_mode_for_presets():
    definitions = catalog().load_builtin_definitions()
    rules = [d for d in definitions if d["mode"] == "rule"]
    assert len(rules) == 8
    assert {d["key"] for d in rules} == {
        "tool_success",
        "stuck_loop",
        "sensitive_leakage",
        "high_risk_command",
        "total_latency",
        "user_interruption",
        "test_execution",
        "rewrite_overlap",
    }
    assert all(d["version"] == "3.0.0" for d in rules)


@pytest.mark.parametrize(
    "key,variant",
    [
        ("task_completion", "text_response"),
        ("task_completion", "executed_artifact"),
        ("report_fidelity", "observed_fact_comparison"),
        ("answer_grounding", "provided_context"),
    ],
)
def test_missing_actual_sources_skip_before_judge(key, variant):
    ex = core(key, variant)
    value = trace()
    value.projection.current_request = TextEvidence("old unsourced query", [])
    ex._judge = object()  # Any accidental invocation fails.
    result = execute_with_applicability(ex, value)
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"


def test_invalid_policy_is_failure_even_when_material_missing():
    ex = core()
    ex.spec["required_evidence"] = {"policy": "unknown"}
    with pytest.raises(ExecutorFailure, match="policy"):
        ex.applicability_skip_reason(trace(request=""))


def test_grounding_ignores_instructions_and_unprovided_retrieval():
    ex = core("answer_grounding", "provided_context")
    value = trace()
    context = ContextEvidence(
        "资料", [SourceReference("t", "s", "attributes.input")], "tool_message"
    )
    value.projection.model_instructions = [context]
    value.projection.retrieval_observations = [context]
    assert ex.applicability_skip_reason(value).startswith("missing_required_evidence")
    value.projection.provided_context = [context]
    assert ex.applicability_skip_reason(value) is None


def test_saved_file_claim_and_generic_tool_success_are_not_artifact_evidence():
    ex = core("task_completion", "executed_artifact")
    action = ToolAction("a", "s", name="tool", status="success", result={"status": "ok"})
    assert ex.applicability_skip_reason(
        trace(coding=True, final="已保存 report.csv", actions=[action])
    )


def test_wrong_target_actual_write_is_judgable_not_prejudged_good():
    ex = core("task_completion", "executed_artifact")
    action = sourced_action(
        "a",
        "s",
        name="write_file",
        arguments={"file_path": "wrong.txt", "content": "{}"},
        result="written",
        status="success",
    )
    assert ex.applicability_skip_reason(trace(coding=True, actions=[action])) is None


def test_report_fidelity_requires_explicit_observed_target_correspondence():
    ex = core("report_fidelity", "observed_fact_comparison")
    action = sourced_action(
        "a",
        "s",
        call_id="c",
        name="shell",
        tool_type="shell",
        arguments={"command": "pytest -q"},
        result={"exit_code": 1},
        status="error",
    )
    assert (
        ex.applicability_skip_reason(trace(coding=True, final="pytest -q 返回0", actions=[action]))
        is None
    )
    assert ex.applicability_skip_reason(trace(coding=True, final="之后终于成功", actions=[action]))


def unittest_action(command="python3 -m unittest test_app.py -v", **kwargs):
    return sourced_action(
        kwargs.pop("action_id", "run"),
        "s",
        call_id=kwargs.pop("call_id", "run-17"),
        name="shell",
        tool_type="shell",
        arguments={"command": command},
        result=kwargs.pop("result", {"exit_code": 0, "output": "Ran 2 tests\nOK"}),
        status=kwargs.pop("status", "success"),
        **kwargs,
    )


class ReportJudge:
    def __init__(self, verdict="good"):
        self.verdict = verdict
        self.bundles = []

    def judge(self, prompt):
        bundle = json.loads(
            prompt.split("<evidence_bundle>", 1)[1].split("</evidence_bundle>", 1)[0]
        )
        self.bundles.append(bundle)
        return {
            "metrics": [
                {
                    "name": "report_fidelity",
                    "verdict": self.verdict,
                    "reason": "核对明确对应的测试结果",
                    "evidence": {
                        "citations": [
                            {"source_id": "request", "quote": "tests"},
                            {"source_id": "final", "quote": "test_app.py"},
                            {"source_id": "observed:0", "quote": bundle["observed:0"]["target"]},
                        ]
                    },
                }
            ]
        }


@pytest.mark.parametrize(
    "command,path",
    [
        ("python3 -m unittest test_app.py -v", "test_app.py"),
        ("python -m unittest test_app.py", "test_app.py"),
        ("python3 -m unittest 'test_app.py'", "test_app.py"),
        ("python3 -m unittest tests/test_app.py", "tests/test_app.py"),
        ("python3 -m unittest ./test_app.py", "./test_app.py"),
        ("python3 -m unittest ../test_app.py", "../test_app.py"),
        ("python3 -m unittest /workspace/test_app.py", "/workspace/test_app.py"),
        *[
            (f"python3 -m unittest {flag} test_app.py", "test_app.py")
            for flag in (
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
            )
        ],
        ("python3 -m unittest -f test_app.py --buffer -v", "test_app.py"),
    ],
)
def test_report_fidelity_unittest_file_alias_passes_original_fact_to_judge(command, path):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    action = unittest_action(command)
    value = trace(
        coding=True,
        request="Run the tests and report the result.",
        final=f"`{path}` passes — 2 tests, OK.",
        actions=[action],
    )
    assert ex.applicability_skip_reason(value) is None
    result = execute_with_applicability(ex, value)
    assert result.metrics[0].verdict.value == "good"
    assert result.metrics[0].evidence["evaluation_scope"] == "observed_fact_comparison"
    assert len(judge.bundles) == 1
    assert set(judge.bundles[0]) == {"request", "final", "observed:0"}
    assert judge.bundles[0]["observed:0"] == {
        "target": command,
        "content": '{"exit_code": 0, "output": "Ran 2 tests\\nOK"}',
        "status": "success",
        "call_id": "run-17",
        "sources": [
            {
                "trace_id": "t",
                "span_id": "s",
                "field_path": "attributes.gen_ai.tool.call.arguments",
                "call_id": None,
            },
            {
                "trace_id": "t",
                "span_id": "s",
                "field_path": "attributes.gen_ai.tool.call.result",
                "call_id": None,
            },
        ],
    }


@pytest.mark.parametrize(
    "final",
    [
        "Fixed the error; test_app.py passes — 2 tests, OK.",
        "'test_app.py' passes — 2 tests, OK.",
        "(test_app.py) passes — 2 tests, OK.",
        '"test_app.py" passes — 2 tests, OK.',
        "`test_app.py`. It passes — 2 tests, OK.",
        "(`test_app.py`) passes — 2 tests, OK.",
    ],
)
def test_report_fidelity_unittest_file_alias_accepts_explicit_plain_or_delimited_path(final):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    result = execute_with_applicability(
        ex,
        trace(
            coding=True,
            request="Run the tests and report the result.",
            final=final,
            actions=[unittest_action()],
        ),
    )
    assert result.metrics[0].verdict.value == "good"
    assert len(judge.bundles) == 1


@pytest.mark.parametrize(
    "final",
    [
        "2 tests, OK.",
        "`other_test_app.py` passes.",
        "`tests/test_app.py` passes.",
        "`./test_app.py` passes.",
        "`../test_app.py` passes.",
        "`/workspace/test_app.py` passes.",
        "`test_app.py.bak` passes.",
        "`test_app.py::TestApp` passes.",
        "`test_app.py/test_other.py` passes.",
        "`my-test_app.py` passes.",
        "`test_app.py-other` passes.",
        "`étest_app.py` passes.",
        "`test_app.pyé` passes.",
    ],
)
def test_report_fidelity_unittest_file_alias_requires_exact_final_path_token(final):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    result = execute_with_applicability(
        ex, trace(coding=True, final=final, actions=[unittest_action()])
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


@pytest.mark.parametrize(
    "other_literal",
    ["other@test_app.py", "test_app.py#backup", "test_app.py=backup", "test_app.py!backup"],
)
def test_report_fidelity_unittest_file_alias_never_extracts_from_different_backtick_literal(
    other_literal,
):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    value = trace(
        coding=True,
        request="Run tests",
        final=f"The file `{other_literal}` passes.",
        actions=[unittest_action()],
    )
    assert (
        ex.applicability_skip_reason(value) == "missing_required_evidence: observed_fact_comparison"
    )
    result = execute_with_applicability(ex, value)
    assert isinstance(result, ApplicabilitySkipped)
    assert judge.bundles == []


@pytest.mark.parametrize(
    "final",
    [
        'The file "other@test_app.py" passes.',
        "The file 'test_app.py#backup' passes.",
        'The file "test_app.py=backup" passes.',
        "The file 'test_app.py!backup' passes.",
        "The file `other test_app.py` passes.",
        'The file "test_app.py other.py" passes.',
        "The file 'test_app.py backup' passes.",
        "The file `test_app.py passes.",
        'The file "test_app.py passes.',
        "The file 'test_app.py passes.",
        "The file test_app.py` passes.",
        "The file other@test_app.py passes.",
        "The file test_app.py#backup passes.",
        "The file test_app.py=backup passes.",
        "The file test_app.py!backup passes.",
        "The file other@`test_app.py` passes.",
        "The file `test_app.py`#backup passes.",
        'The file "`test_app.py`" passes.',
        "The file `test_app.py!` passes.",
        "The file test_app.py. passes.",
    ],
)
def test_report_fidelity_unittest_file_alias_preserves_unknown_or_unterminated_token(final):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    value = trace(coding=True, request="Run tests", final=final, actions=[unittest_action()])
    assert (
        ex.applicability_skip_reason(value) == "missing_required_evidence: observed_fact_comparison"
    )
    result = execute_with_applicability(ex, value)
    assert isinstance(result, ApplicabilitySkipped)
    assert judge.bundles == []


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m unittest tests/test_app.py",
        "python3 -m unittest ./test_app.py",
        "python3 -m unittest /workspace/test_app.py",
        "python3 -m unittest Test_App.py",
    ],
)
def test_report_fidelity_unittest_file_alias_never_normalizes_observed_path(command):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    result = execute_with_applicability(
        ex,
        trace(
            coding=True,
            final="`test_app.py` passes — 2 tests, OK.",
            actions=[unittest_action(command)],
        ),
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m unittest discover test_app.py",
        "python3 -m unittest test_app",
        "python3 -m unittest test_app.TestApp",
        "python3 -m unittest test_app.py other.py",
        "python3 -m unittest test_app.py test_app.py",
        "python3 -m unittest -k test_app.py",
        "python3 -m unittest -vq test_app.py",
        "python3 -m unittest --catchbreak test_app.py",
        "python3 -m unittest -- test_app.py",
        "python3 -m unittest 'test_app.py",
        "python3 -m unittest test_app.py && true",
        "python3 -m unittest test_app.py; true",
        "python3 -m unittest test_app.py | cat",
        "python3 -m unittest test_app.py > output.txt",
        "python3 -m unittest test_app.py\ntrue",
        "python3 -m unittest $TEST_FILE test_app.py",
        "python3 -m unittest $(echo test_app.py)",
        "python3 -m unittest `echo test_app.py`",
        "python3 -m unittest test_*.py",
        "python3 -m unittest test_{app,other}.py",
        "python3 -m unittest 'test_app.py' '?'",
        "python3 -m unittest ~/test_app.py",
        "python3 -m unittest test_app\\.py",
        "python3 -m unittest (test_app.py)",
        "env python3 -m unittest test_app.py",
        "timeout 5 python3 -m unittest test_app.py",
        "/usr/bin/python3 -m unittest test_app.py",
        "python3 -I -m unittest test_app.py",
        "python3 -m pytest test_app.py",
    ],
)
def test_report_fidelity_unittest_file_alias_rejects_unsupported_command(command):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    result = execute_with_applicability(
        ex,
        trace(
            coding=True,
            final="`test_app.py` passes — 2 tests, OK.",
            actions=[unittest_action(command)],
        ),
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


@pytest.mark.parametrize(
    "final",
    [
        "`test_app.py` passes — 2 tests, OK.",
        "The last test_app.py run passed.",
        "All test_app.py runs passed.",
    ],
)
@pytest.mark.parametrize("first_status", ["error", "success"])
def test_report_fidelity_unittest_file_alias_does_not_choose_between_repeated_events(
    final, first_status
):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    first = unittest_action(
        result={"exit_code": 1 if first_status == "error" else 0}, status=first_status
    )
    second = unittest_action(
        "python -m unittest -q test_app.py", action_id="next", call_id="run-18"
    )
    result = execute_with_applicability(
        ex, trace(coding=True, final=final, actions=[first, second])
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


@pytest.mark.parametrize(
    "ineligible", ["unsourced", "input_truncated", "output_truncated", "empty_result"]
)
def test_report_fidelity_unittest_file_alias_keeps_original_material_gate(ineligible):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    action = unittest_action()
    if ineligible == "unsourced":
        action = replace(action, field_sources={})
    elif ineligible == "empty_result":
        action = replace(action, result={})
    else:
        action = replace(action, **{ineligible: True})
    result = execute_with_applicability(
        ex, trace(coding=True, final="`test_app.py` passes — 2 tests, OK.", actions=[action])
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


@pytest.mark.parametrize(
    "ineligible", ["unsourced", "input_truncated", "output_truncated", "empty_result"]
)
def test_report_fidelity_unittest_file_alias_dropped_later_event_still_blocks_ambiguity(ineligible):
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    first = unittest_action(result={"exit_code": 1}, status="error")
    later = unittest_action(
        "python3 -m unittest -q test_app.py", action_id="later", call_id="run-18"
    )
    if ineligible == "unsourced":
        later = replace(later, field_sources={})
    elif ineligible == "empty_result":
        later = replace(later, result={})
    else:
        later = replace(later, **{ineligible: True})
    value = trace(
        coding=True,
        request="Run the tests and report the result.",
        final="`test_app.py` passes — 2 tests, OK.",
        actions=[first, later],
    )
    assert (
        ex.applicability_skip_reason(value) == "missing_required_evidence: observed_fact_comparison"
    )
    result = execute_with_applicability(ex, value)
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


def test_report_fidelity_unittest_file_alias_does_not_prejudge_failed_event_good():
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge(verdict="bad")
    ex._judge = judge
    result = execute_with_applicability(
        ex,
        trace(
            coding=True,
            request="Run the tests and report the result.",
            final="`test_app.py` passes — 2 tests, OK.",
            actions=[unittest_action(result={"exit_code": 1, "output": "FAILED"}, status="error")],
        ),
    )
    assert not isinstance(result, ApplicabilitySkipped)
    assert result.metrics[0].verdict.value == "bad"
    assert len(judge.bundles) == 1
    assert judge.bundles[0]["observed:0"]["status"] == "error"
    assert judge.bundles[0]["observed:0"]["content"] == '{"exit_code": 1, "output": "FAILED"}'


@pytest.mark.parametrize("mention", ["command", "call_id", "both"])
def test_report_fidelity_unittest_file_alias_preserves_command_and_call_matching_without_duplicate(
    mention,
):
    ex = core("report_fidelity", "observed_fact_comparison")
    action = unittest_action()
    final = {
        "command": action.arguments["command"],
        "call_id": "run-17: `test_app.py` passes.",
        "both": action.arguments["command"] + " run-17: `test_app.py` passes.",
    }[mention]
    value = trace(coding=True, final=final, actions=[action])
    assert ex.applicability_skip_reason(value) is None
    bundle = ex._extra_prompt_inputs(value, value.projection)["evidence_bundle"]
    assert set(bundle) == {"request", "final", "observed:0"}
    assert bundle["observed:0"]["target"] == action.arguments["command"]


@pytest.mark.parametrize("mention", ["command", "call_id"])
def test_report_fidelity_unittest_file_alias_ambiguity_keeps_explicit_original_correspondence(
    mention,
):
    ex = core("report_fidelity", "observed_fact_comparison")
    first = unittest_action()
    later = unittest_action(
        "python3 -m unittest -q test_app.py", action_id="later", call_id="run-18"
    )
    final = first.arguments["command"] if mention == "command" else "run-17: `test_app.py` passes."
    value = trace(coding=True, final=final, actions=[first, later])
    assert ex.applicability_skip_reason(value) is None
    bundle = ex._extra_prompt_inputs(value, value.projection)["evidence_bundle"]
    assert set(bundle) == {"request", "final", "observed:0"}
    assert bundle["observed:0"]["call_id"] == "run-17"
    assert bundle["observed:0"]["target"] == first.arguments["command"]


@pytest.mark.parametrize("mention", ["command", "call_id"])
def test_report_fidelity_unittest_file_alias_preserves_unsupported_command_original_matching(
    mention,
):
    ex = core("report_fidelity", "observed_fact_comparison")
    action = unittest_action("env python3 -m unittest test_app.py")
    final = action.arguments["command"] if mention == "command" else "run-17: `test_app.py` passes."
    value = trace(coding=True, final=final, actions=[action])
    assert ex.applicability_skip_reason(value) is None
    bundle = ex._extra_prompt_inputs(value, value.projection)["evidence_bundle"]
    assert set(bundle) == {"request", "final", "observed:0"}
    assert bundle["observed:0"]["target"] == action.arguments["command"]


def test_report_fidelity_unittest_file_alias_cannot_infer_uncaptured_execution():
    ex = core("report_fidelity", "observed_fact_comparison")
    judge = ReportJudge()
    ex._judge = judge
    result = execute_with_applicability(
        ex, trace(coding=True, final="`test_app.py` passes — 2 tests, OK.")
    )
    assert isinstance(result, ApplicabilitySkipped)
    assert result.code == "missing_required_evidence"
    assert judge.bundles == []


def test_typed_facts_without_original_field_sources_do_not_invent_provenance():
    ex = core("task_completion", "executed_artifact")
    action = ToolAction(
        "a",
        "s",
        name="write_file",
        arguments={"file_path": "report.csv", "content": "a,b"},
        result="written",
        status="success",
    )
    assert ex.applicability_skip_reason(trace(coding=True, actions=[action])).startswith(
        "missing_required_evidence"
    )


def test_bundle_uses_recorded_raw_fields_instead_of_invented_fact_field_paths():
    ex = core("task_completion", "executed_artifact")
    action = sourced_action(
        "a",
        "s",
        name="write_file",
        arguments={"file_path": "report.csv", "content": "a,b"},
        result="written",
        status="success",
    )
    value = trace(coding=True, actions=[action])
    bundle = ex._extra_prompt_inputs(value, value.projection)["evidence_bundle"]
    paths = {s["field_path"] for s in bundle["artifact:0"]["sources"]}
    assert paths == {
        "attributes.gen_ai.tool.call.arguments",
        "attributes.gen_ai.tool.call.result",
        "status_code",
    }


def test_judge_cannot_invent_citations_or_metric_skip():
    ex = core()

    class FakeJudge:
        def judge(self, prompt):
            return {
                "metrics": [
                    {
                        "name": "task_completion",
                        "verdict": "good",
                        "reason": "符合文本要求",
                        "evidence": {"citations": [{"source_id": "invented", "quote": "不存在"}]},
                    }
                ]
            }

    ex._judge = FakeJudge()
    with pytest.raises(ExecutorFailure, match="citation"):
        execute_with_applicability(ex, trace())


def test_text_good_result_retains_explicit_external_execution_boundary():
    ex = core()

    class FakeJudge:
        def judge(self, prompt):
            assert "给我两列CSV" in prompt and "name,value" in prompt
            return {
                "metrics": [
                    {
                        "name": "task_completion",
                        "verdict": "good",
                        "reason": "满足两列文本要求",
                        "evidence": {
                            "citations": [
                                {"source_id": "request", "quote": "两列CSV"},
                                {"source_id": "final", "quote": "name,value"},
                            ]
                        },
                    }
                ]
            }

    ex._judge = FakeJudge()
    result = execute_with_applicability(ex, trace())
    assert "外部执行未在此项验证" in result.metrics[0].reason
    assert result.metrics[0].evidence["evaluation_scope"] == "text_response"


def test_core_rejects_empty_semantic_reason_even_with_locatable_citations():
    ex = core()

    class EmptyReasonJudge:
        def judge(self, prompt):
            return {
                "metrics": [
                    {
                        "name": "task_completion",
                        "verdict": "good",
                        "reason": " ",
                        "evidence": {
                            "citations": [
                                {"source_id": "request", "quote": "两列CSV"},
                                {"source_id": "final", "quote": "name,value"},
                            ]
                        },
                    }
                ]
            }

    ex._judge = EmptyReasonJudge()
    with pytest.raises(ExecutorFailure, match="reason"):
        execute_with_applicability(ex, trace())
