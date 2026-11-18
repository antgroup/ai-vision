"""Counterexamples for factual signals and honest post-mutation verification."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agentlens.diagnoser.base import ApplicabilitySkipped, execute_with_applicability
from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.enums import Verdict
from agentlens.service.datasource.projection.models import (
    SourceReference,
    TextEvidence,
    TraceOutcome,
)
from agentlens.service.datasource.projection.projectors.coding.models import (
    CodingProjection,
    ToolAction,
)
from agentlens.service.datasource.trace import Trace


def action(name="Bash", arguments=None, result=None, status="success", span="source"):
    return ToolAction(
        action_id=span or "unlocated",
        source_span_id=span,
        name=name,
        arguments=arguments,
        result=result,
        status=status,
    )


def evaluate(key, actions=(), **facts):
    policy = facts.pop("policy", {})
    projection = CodingProjection(
        timestamp=None,
        user_query="",
        agent_output="",
        labels={},
        outcome=TraceOutcome(False, None, facts.pop("latency_ms", None), False),
        tool_actions=list(actions),
        **facts,
    )
    trace = Trace(trace_id="semantic-case", runtime_type="codex", spans=[], projection=projection)
    definition = BuiltinRuleRegistry.get(key).to_builtin_definition()
    definition["spec"].update(policy)
    result = execute_with_applicability(
        ExecutorRegistry.create("rule", key, definition["spec"]), trace
    )
    return result if isinstance(result, ApplicabilitySkipped) else result.metrics[0]


def edit(*, status="success", span="edit", diff=True):
    args = {"file_path": "app.py"}
    if diff:
        args.update(old_string="return a - b", new_string="return a + b")
    return action(
        "Edit", args, {"success": status == "success"} if status != "unknown" else {}, status, span
    )


def check(command="python -m unittest test_app.py", exit_code=0, span="check"):
    return action(arguments={"command": command}, result={"exit_code": exit_code}, span=span)


def test_any_failed_tool_is_visible_among_three_successes():
    metric = evaluate(
        "tool_success", [check(span=str(i)) for i in range(3)] + [check(exit_code=1, span="failed")]
    )
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["counts"] == {"success": 3, "failure": 1, "unknown": 0}
    assert metric.evidence["bad_spans"][0]["span_id"] == "failed"


@pytest.mark.parametrize("boundary", [action("poll"), action("write_stdin", {"session_id": "1"})])
def test_polling_breaks_repeat_adjacency(boundary):
    read = action("Read", {"file_path": "app.py"}, "same")
    assert evaluate("stuck_loop", [read, read, boundary, read]).verdict is Verdict.GOOD


def test_changed_read_results_are_progress():
    reads = [
        action("Read", {"file_path": "app.py"}, f"revision {i}", span=f"read-{i}") for i in range(3)
    ]
    assert evaluate("stuck_loop", reads).verdict is Verdict.GOOD


@pytest.mark.parametrize("outputs", [("", "ready", "ready"), ("ready", "", "")])
def test_review_acquired_empty_result_change_is_progress(outputs):
    reads = [action("Read", {"file_path": "app.py"}, output) for output in outputs]
    assert evaluate("stuck_loop", reads).verdict is Verdict.GOOD


@pytest.mark.parametrize(
    "outputs",
    [
        (None, "ready", "ready"),
        ({"stdout": None}, {"stdout": "ready"}, {"stdout": "ready"}),
        ({"duration_ms": 1}, {"duration_ms": 2}, {"duration_ms": 3}),
        ("", "", ""),
    ],
)
def test_review_missing_or_metadata_only_result_does_not_invent_progress(outputs):
    reads = [action("Read", {"file_path": "app.py"}, output) for output in outputs]
    assert evaluate("stuck_loop", reads).verdict is Verdict.BAD


def test_review_transport_envelope_without_output_does_not_invent_progress():
    reads = [
        action(
            "Read",
            {"file_path": "app.py"},
            f"Chunk ID: {index}\nWall time: {index + 1}s\nProcess exited with code 0\n",
        )
        for index in range(3)
    ]
    assert evaluate("stuck_loop", reads).verdict is Verdict.BAD


def test_host_shell_timing_envelopes_are_not_file_progress():
    reads = [
        action(
            arguments={"command": "cat app.py"},
            result=(
                f"Chunk ID: chunk-{i}\nWall time: {i + 1} seconds\n"
                "Process exited with code 0\nFinal output:\nreturn a + b\n"
            ),
            span=f"read-{i}",
        )
        for i in range(3)
    ]
    metric = evaluate("stuck_loop", reads)
    assert metric.verdict is Verdict.BAD
    assert {entry["span_id"] for entry in metric.evidence["bad_spans"]} == {
        "read-0",
        "read-1",
        "read-2",
    }


def _platform_output_wrapper(chunk, body=None):
    header = (
        f"Chunk ID: {chunk}\nWall time: 0.0000 seconds\n"
        "Process exited with code 0\nOriginal token count: 8\n"
    )
    return header if body is None else header + "Output:\n" + body


@pytest.mark.parametrize(
    "bodies,expected",
    [
        (("def add(a, b):\n    return a + b\n",) * 3, Verdict.BAD),
        (("", "", ""), Verdict.BAD),
        ((None, None, None), Verdict.BAD),
        (("", "ready", "ready"), Verdict.GOOD),
        (("ready", "", ""), Verdict.GOOD),
        ((None, "ready", "ready"), Verdict.BAD),
        (("revision one", "revision two", "revision two"), Verdict.GOOD),
    ],
)
def test_platform_transport_output_body_controls_repetition(bodies, expected):
    calls = [
        action(
            arguments={"command": "cat app.py"},
            result=_platform_output_wrapper(chunk, body),
            span=f"read-{index}",
        )
        for index, (chunk, body) in enumerate(
            zip(("cefe78", "12b0ea", "a22296"), bodies, strict=True)
        )
    ]
    metric = evaluate("stuck_loop", calls)
    assert metric.verdict is expected
    assert metric.evidence["repeat_count"] == (3 if expected is Verdict.BAD else 0)
    if expected is Verdict.BAD:
        assert {f["source_span_id"] for f in metric.evidence["findings"]} == {
            "read-0",
            "read-1",
            "read-2",
        }


@pytest.mark.parametrize("marker", ["Output:", "Final output:"])
def test_output_labels_inside_ordinary_user_body_are_not_transport_headers(marker):
    calls = [
        action("Read", {"file_path": "app.py"}, f"revision {index}\n{marker}\nsame")
        for index in range(3)
    ]
    assert evaluate("stuck_loop", calls).verdict is Verdict.GOOD


@pytest.mark.parametrize(
    "commands,directories,expected",
    [
        (
            ("cat 'two  spaces.py'", "cat 'two spaces.py'", "cat 'two spaces.py'"),
            ("/repo/a",) * 3,
            Verdict.GOOD,
        ),
        (("cat app.py",) * 3, ("/repo/a", "/repo/b", "/repo/c"), Verdict.GOOD),
        (("cat 'two  spaces.py'",) * 3, ("/repo/a",) * 3, Verdict.BAD),
    ],
)
def test_platform_repeat_identity_preserves_quoted_target_and_acquired_directory(
    commands, directories, expected
):
    calls = [
        action(
            arguments={"command": command, "cwd": directory},
            result=_platform_output_wrapper(chunk, "identical actual content"),
        )
        for command, directory, chunk in zip(
            commands, directories, ("cefe78", "12b0ea", "a22296"), strict=True
        )
    ]
    metric = evaluate("stuck_loop", calls)
    assert metric.verdict is expected
    assert metric.evidence["repeat_count"] == (3 if expected is Verdict.BAD else 0)


def test_unlocated_risk_is_a_finding_with_unknown_authorization():
    metric = evaluate(
        "high_risk_command",
        [action(arguments={"command": "chmod 666 app.py"}, result={"exit_code": 0}, span=None)],
    )
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["findings"][0]["location_status"] == "unavailable"
    assert metric.evidence["findings"][0]["authorization"] == "unknown"


@pytest.mark.parametrize("channel", ["current_request", "final_output"])
def test_unlocated_credentials_are_masked_and_channel_bound(channel):
    secret = "Bearer k9T7m4R2v8Q6n3X5p1C0z7L9w4H2"
    metric = evaluate("sensitive_leakage", **{channel: TextEvidence(secret, [])})
    assert metric.verdict is Verdict.BAD
    assert secret not in str(metric.evidence) + metric.reason
    assert metric.evidence["findings"][0]["channel"] == channel
    assert metric.evidence["findings"][0]["location_status"] == "unavailable"


def test_review_same_span_credentials_keep_input_and_final_output_channels():
    secret = "Bearer k9T7m4R2v8Q6n3X5p1C0z7L9w4H2"
    metric = evaluate(
        "sensitive_leakage",
        current_request=TextEvidence(secret, [SourceReference("trace", "turn", "input")]),
        final_output=TextEvidence(secret, [SourceReference("trace", "turn", "output")]),
    )
    assert metric.verdict is Verdict.BAD
    assert secret not in str(metric.evidence) + metric.reason
    assert {(f["channel"], f["attribution"]) for f in metric.evidence["findings"]} == {
        ("current_request", "user_provided"),
        ("final_output", "visible_agent_output"),
    }
    assert len(metric.evidence["bad_spans"][0]["findings"]) == 2


@pytest.mark.parametrize(
    "first_cwd,second_cwd", [("/repo/a", "/repo/b"), (None, "/repo/a"), (None, None)]
)
def test_review_tool_recovery_requires_same_known_execution_context(first_cwd, second_cwd):
    calls = [
        action(
            arguments={
                "command": "python -m unittest test_app.py",
                **({"cwd": cwd} if cwd else {}),
            },
            result={"exit_code": code},
        )
        for cwd, code in [(first_cwd, 1), (second_cwd, 0)]
    ]
    metric = evaluate("tool_success", calls)
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["findings"][0]["recovery"] == "unknown"


def test_review_tool_same_directory_matching_success_is_observed_recovery():
    calls = [
        action(arguments={"command": "pytest -q", "workdir": "/repo/a"}, result={"exit_code": code})
        for code in (1, 0)
    ]
    metric = evaluate("tool_success", calls)
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["findings"][0]["recovery"] == "matching_success_observed"


@pytest.mark.parametrize("key", ["tool_success", "test_execution"])
def test_review_quoted_target_whitespace_does_not_match_another_check(key):
    calls = [
        action(arguments={"command": command, "cwd": "/repo/a"}, result={"exit_code": code})
        for command, code in [
            ("pytest 'tests/test_two  spaces.py'", 1),
            ("pytest 'tests/test_two spaces.py'", 0),
        ]
    ]
    metric = evaluate(key, [edit(), *calls])
    assert metric.verdict is Verdict.BAD
    if key == "tool_success":
        assert metric.evidence["findings"][0]["recovery"] == "unknown"
    else:
        assert metric.evidence["verification"]["result"] == "failed"


def test_review_same_quoted_target_rerun_can_resolve_failed_verification():
    command = "pytest 'tests/test_two  spaces.py'"
    assert (
        evaluate("test_execution", [edit(), check(command, 1), check(command)]).verdict
        is Verdict.GOOD
    )


def test_elapsed_time_is_a_measurement_without_default_quality_budget():
    metric = evaluate("total_latency", latency_ms=1_501_000)
    assert metric.verdict is Verdict.GOOD
    assert metric.evidence["total_latency_ms"] == 1_501_000
    assert metric.evidence["total_latency_seconds"] == 1501


def test_explicit_advanced_rate_never_removes_failed_action_evidence():
    metric = evaluate(
        "tool_success",
        [check(span=str(i)) for i in range(3)] + [check(exit_code=1, span="failed")],
        policy={"tool_success_min_rate": 0.75},
    )
    assert metric.verdict is Verdict.GOOD
    assert metric.evidence["counts"]["failure"] == 1
    assert metric.evidence["bad_spans"][0]["span_id"] == "failed"


def test_explicit_finite_latency_budget_is_a_signal_with_actual_measurement():
    metric = evaluate("total_latency", latency_ms=11_000, policy={"latency_budget_ms": 10_000})
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["total_latency_ms"] == 11_000
    assert metric.evidence["latency_budget_ms"] == 10_000


@pytest.mark.parametrize(
    "key,policy",
    [
        ("total_latency", {"latency_budget_ms": float("inf")}),
        ("total_latency", {"latency_budget_ms": -1}),
        ("tool_success", {"tool_success_min_rate": float("nan")}),
        ("tool_success", {"tool_success_min_rate": True}),
    ],
)
def test_nonfinite_or_invalid_policy_does_not_become_a_skip_or_good(key, policy):
    with pytest.raises(ValueError):
        evaluate(key, [check()], latency_ms=10_000, policy=policy)


def test_absent_interruption_is_unknown_but_explicit_false_is_observed():
    assert isinstance(evaluate("user_interruption"), ApplicabilitySkipped)
    metric = evaluate("user_interruption", observed_user_interruption=False)
    assert metric.verdict is Verdict.GOOD
    assert metric.evidence["interrupted"] is False


@pytest.mark.parametrize(
    "checks",
    [
        [check(exit_code=1)],
        [check(), check(exit_code=1)],
        [check(exit_code=1), check("ruff check app.py")],
    ],
)
def test_failed_check_is_not_repaired_by_earlier_pass_or_unrelated_lint(checks):
    metric = evaluate("test_execution", [edit(), *checks])
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["verification"]["result"] == "failed"


def test_matching_successful_rerun_can_resolve_failure():
    assert evaluate("test_execution", [edit(), check(exit_code=1), check()]).verdict is Verdict.GOOD


@pytest.mark.parametrize("field", ["cwd", "workdir"])
def test_pass_in_another_explicit_working_directory_cannot_resolve_failure(field):
    checks = [
        action(
            arguments={"command": "python -m unittest test_app.py", field: directory},
            result={"exit_code": code},
            span=span,
        )
        for directory, code, span in [("/repo/a", 1, "failed-a"), ("/repo/b", 0, "passed-b")]
    ]
    metric = evaluate("test_execution", [edit(), *checks])
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["verification"]["result"] == "failed"
    assert metric.evidence["bad_spans"][0]["span_id"] == "failed-a"
    assert metric.evidence["verification"]["execution_context"] == {field: "/repo/a"}


def test_matching_check_in_same_explicit_working_directory_resolves_failure():
    checks = [
        action(
            arguments={"command": "python -m unittest test_app.py", "cwd": "/repo/a"},
            result={"exit_code": code},
        )
        for code in (1, 0)
    ]
    assert evaluate("test_execution", [edit(), *checks]).verdict is Verdict.GOOD


def test_unknown_matching_rerun_cannot_hide_known_failed_verification():
    unknown = action(
        arguments={"command": "python -m unittest test_app.py"}, result={}, status="unknown"
    )
    metric = evaluate("test_execution", [edit(), check(exit_code=1), unknown])
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["verification"]["result"] == "failed"


def test_structured_host_metadata_does_not_prove_repeated_call_progress():
    reads = [
        action("Read", {"file_path": "app.py"}, {"duration_ms": index, "exit_code": 0})
        for index in range(3)
    ]
    assert evaluate("stuck_loop", reads).verdict is Verdict.BAD


def test_normal_wait_calls_are_not_stuck_loop_findings():
    waits = [action("Wait", {"session_id": "one"}, "waiting") for _ in range(3)]
    assert evaluate("stuck_loop", waits).verdict is Verdict.GOOD


def test_uncertain_write_invalidates_prior_verification():
    metric = evaluate(
        "test_execution", [edit(), check(), edit(status="unknown", span="uncertain", diff=False)]
    )
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["verification"]["result"] == "missing"


def test_uncertain_only_write_still_requires_verification():
    metric = evaluate("test_execution", [edit(status="unknown", diff=False)])
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["verification"]["result"] == "missing"


def test_hidden_explicit_edit_invalidates_an_earlier_check():
    hidden_edit = action("Edit", "[REDACTED]", "[REDACTED]", "success", "hidden-edit")
    metric = evaluate("test_execution", [edit(), check(), hidden_edit])
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.BAD


def test_unknown_verification_is_a_diagnoser_skip():
    unknown = action(arguments={"command": "pytest -q"}, result={}, status="unknown")
    assert isinstance(evaluate("test_execution", [edit(), unknown]), ApplicabilitySkipped)


def test_successful_tool_status_does_not_replace_explicit_unknown_check_result():
    unknown = action("RunTests", result={"verification_result": "unknown"}, status="success")
    assert isinstance(evaluate("test_execution", [edit(), unknown]), ApplicabilitySkipped)


def test_masked_compound_check_result_cannot_claim_a_pass():
    patch = (
        "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: app.py\n@@\n"
        "-return a - b\n+return a + b\n*** End Patch\nPATCH\n"
    )
    metric = evaluate("test_execution", [check(patch + "pytest -q; true")])
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.BAD


def test_successful_lint_cannot_mask_failing_test_inside_one_shell_action():
    metric = evaluate("test_execution", [edit(), check("pytest -q; ruff check app.py")])
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.BAD


def test_failed_compound_action_does_not_erase_possible_mutation():
    patch = (
        "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: app.py\n@@\n"
        "-return a - b\n+return a + b\n*** End Patch\nPATCH\n"
    )
    metric = evaluate("test_execution", [check(patch + "pytest -q", exit_code=1)])
    assert not (isinstance(metric, ApplicabilitySkipped) and metric.code == "not_applicable")
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.BAD


def test_failed_and_noop_edits_do_not_make_rewrite_findings():
    failed = edit(status="error")
    noop = replace(edit(), arguments={"file_path": "app.py", "old_string": "x", "new_string": "x"})
    assert isinstance(
        evaluate("rewrite_overlap", [failed, failed, noop, noop]), ApplicabilitySkipped
    )


def test_failed_verification_fixture_exit_reaches_projection_and_rule(tmp_path):
    fixture_dir = (
        Path(__file__).resolve().parents[4] / "ai-vision/agent-plugins/tests/diagnostics_e2e"
    )
    sys.path.insert(0, str(fixture_dir))
    try:
        from scenarios import prepare_workspace, scenarios

        case = next(
            case for case in scenarios(include_extended=True) if case.name == "failed-verification"
        )
        workspace = prepare_workspace(tmp_path / "program", case)
        native = []
        for step in case.tool_steps:
            if step["op"] == "edit":
                path = workspace / step["path"]
                path.write_text(path.read_text().replace(step["old"], step["new"]))
                native.append(
                    action(
                        "Edit",
                        {
                            "file_path": step["path"],
                            "old_string": step["old"],
                            "new_string": step["new"],
                        },
                        {"success": True},
                        span="actual-edit",
                    )
                )
        assert (workspace / "app.py").read_text() == case.final_files["app.py"]
        failed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "test_app.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert failed.returncode != 0 and "FAILED" in failed.stderr
        native.append(check(exit_code=failed.returncode, span="actual-test"))
        metric = evaluate("test_execution", native)
        assert metric.verdict is Verdict.BAD
        assert metric.evidence["verification"]["result"] == "failed"
        repair = next(
            case
            for case in scenarios(include_extended=True)
            if case.name == "repair-with-verification"
        )
        step = next(step for step in repair.tool_steps if step["op"] == "edit")
        path = workspace / step["path"]
        path.write_text(path.read_text().replace(step["old"], step["new"]))
        passed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "test_app.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert passed.returncode == 0 and "OK" in passed.stderr
        native.extend(
            [
                action(
                    "Edit",
                    {
                        "file_path": step["path"],
                        "old_string": step["old"],
                        "new_string": step["new"],
                    },
                    {"success": True},
                    span="actual-repair",
                ),
                check(exit_code=passed.returncode, span="actual-retest"),
            ]
        )
        assert evaluate("test_execution", native).verdict is Verdict.GOOD
    finally:
        sys.path.remove(str(fixture_dir))


def test_real_failed_unittest_can_be_masked_by_successful_shell_tail(tmp_path):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b - 1\n")
    (tmp_path / "test_app.py").write_text(
        "import unittest\nfrom app import add\n"
        "class Check(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    )
    command = f"{sys.executable} -B -m unittest test_app.py; true"
    actual = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True)
    assert actual.returncode == 0 and "FAILED" in actual.stderr
    metric = evaluate(
        "test_execution",
        [
            edit(),
            action(
                arguments={"command": command},
                result={
                    "exit_code": actual.returncode,
                    "stdout": actual.stdout,
                    "stderr": actual.stderr,
                },
            ),
        ],
    )
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.BAD


def test_known_failure_and_unknown_status_have_separate_counts():
    unknown = action("Search", {"query": "ordinary empty results"}, "", "unknown", "unknown")
    metric = evaluate("tool_success", [check(exit_code=1, span="failure"), unknown])
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["counts"] == {"success": 0, "failure": 1, "unknown": 1}
    assert metric.evidence["findings"][0]["recovery"] == "unknown"


def test_empty_search_success_is_an_observed_success():
    metric = evaluate("tool_success", [action("Search", {"query": "absent"}, "", "success")])
    assert metric.verdict is Verdict.GOOD
    assert metric.evidence["counts"] == {"success": 1, "failure": 0, "unknown": 0}


def test_unknown_risky_execution_cannot_claim_a_confirmed_absence():
    unknown = action(arguments={"command": "chmod 666 app.py"}, result={}, status="unknown")
    assert isinstance(evaluate("high_risk_command", [unknown]), ApplicabilitySkipped)


def test_visible_credential_hit_wins_over_another_hidden_channel():
    secret = "Bearer k9T7m4R2v8Q6n3X5p1C0z7L9w4H2"
    metric = evaluate(
        "sensitive_leakage",
        current_request=TextEvidence(secret, []),
        final_output=TextEvidence("[REDACTED]", []),
    )
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["findings"][0]["attribution"] == "user_provided"


def test_rewrite_reports_actual_pairs_and_files_without_claiming_no_progress():
    statements = [
        "return a - b",
        "return a + b - 1",
        "return a + b + 1",
        "return a + b + 0",
        "return a + b",
    ]
    edits = [
        action(
            "Edit",
            {"file_path": "app.py", "old_string": before, "new_string": after},
            {"success": True},
            span=f"edit-{index}",
        )
        for index, (before, after) in enumerate(zip(statements, statements[1:], strict=False))
    ]
    metric = evaluate("rewrite_overlap", edits)
    assert metric.evidence["overlap_pair_count"] == 3
    assert metric.evidence["files"] == ["app.py"]
    assert metric.evidence["progress"] == "unknown"


def test_tiny_common_token_rewrites_do_not_claim_overlap():
    edits = [
        action(
            "Edit",
            {"file_path": "app.py", "old_string": before, "new_string": after},
            {"success": True},
        )
        for before, after in [("x", "y"), ("y", "x"), ("x", "y"), ("y", "x")]
    ]
    metric = evaluate("rewrite_overlap", edits)
    assert isinstance(metric, ApplicabilitySkipped) or metric.verdict is Verdict.GOOD
