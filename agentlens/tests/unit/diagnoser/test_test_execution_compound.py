"""Same-action verification requires ordered executable commands, not words."""

import itertools
import shlex
import subprocess
import sys

import pytest

from agentlens.diagnoser.executors.rule.builtin.coding.test_execution import (
    TestExecutionExecutor as Executor,
)
from agentlens.domain.enums import Verdict
from tests.unit.diagnoser.projection_factory import trace_from_dict

PATCH = (
    "apply_patch <<'PATCH'\n"
    "*** Begin Patch\n*** Update File: app.py\n@@\n"
    " def add(a, b):\n-    return a - b\n+    return a + b\n"
    "*** End Patch\nPATCH\n"
)
REAL_COMMAND = (
    PATCH + "printf '%s\\n' '--- app.py after edit ---'\n"
    "sed -n '1,100p' app.py\nprintf '%s\\n' '--- tests ---'\npython test_app.py"
)


def evaluate(command, *, later_command=None, output=None):
    steps = [
        {
            "kind": "execution",
            "name": "Bash",
            "type": "TOOL",
            "status": "OK",
            "ref": "real-mutation-span",
            "input": {"command": command},
            "output": output
            or "Process exited with code 0\nSuccess. Updated app.py\nRan 2 tests\nOK",
        }
    ]
    if later_command:
        steps.append(
            {
                "kind": "execution",
                "name": "Bash",
                "type": "TOOL",
                "status": "OK",
                "input": {"command": later_command},
                "output": {"exit_code": 0},
            }
        )
    trace = trace_from_dict(
        {
            "trace_id": "same-action",
            "runtime_type": "codex",
            "steps": steps,
            "outcome": {"has_error": False, "result_state": "SUCCESS", "interrupted": False},
        }
    )
    return Executor(
        {"metrics": [{"name": "test_execution_rate", "desc": "test execution"}]}
    ).evaluate(trace)


@pytest.mark.parametrize(
    "command",
    [
        REAL_COMMAND,
        PATCH + "pytest -q",
        PATCH + "pytest --setup-show -q",
        PATCH + "pytest --trace-config -q",
        PATCH + "# pytest before this line is only a comment\npython test_app.py",
        PATCH + "printf '%s\\n' 'pytest; edit is a string'\npython test_app.py",
        PATCH + "'python' 'test_app.py'",
        PATCH.replace("<<'PATCH'", "<<PATCH") + "python test_app.py",
        PATCH.replace("<<'PATCH'", '<<-"PATCH"').replace("\nPATCH\n", "\n\tPATCH\n")
        + "python test_app.py",
        "bash -lc " + shlex.quote(REAL_COMMAND),
        PATCH + "sed -i 's/a + b/a * b/' app.py; python test_app.py",
        PATCH + "pytest && python -m unittest -q",
    ],
)
def test_confirmed_compound_edit_then_executable_verification_is_good(command):
    result = evaluate(command)
    metric = result.metric("test_execution_rate")
    assert metric.verdict is Verdict.GOOD
    assert metric.evidence["verification"]["command"] == command
    assert metric.evidence["verification"]["result"] == "passed"
    assert "bad_spans" not in metric.evidence


@pytest.mark.parametrize(
    "command",
    [
        "python test_app.py\n" + PATCH,
        PATCH + "python test_app.py\nsed -i 's/a + b/a * b/' app.py",
        PATCH + "python test_app.py\nrm app.py",
        PATCH + "echo 'python test_app.py'",
        PATCH + "printf '%s\\n' 'pytest; python test_app.py'",
        PATCH + "cat <<'DOC'\npython test_app.py\nDOC\n",
        PATCH + "# python test_app.py\ntrue",
        PATCH + "'python test_app.py'",
        PATCH + "python app.py 'test_app.py'",
        PATCH + 'python "app.py test_app.py"',
        PATCH + 'python -m "unittest fake-module"',
        PATCH + "unknown-pytest",
        PATCH + "python test_app.py; rg --pre 'sed -i s/x/y/ app.py' text app.py",
        PATCH + "python -c \"print('python test_app.py')\"",
        PATCH + "python test_app.py || true",
        PATCH + "false && python test_app.py; true",
        PATCH + "false && python test_app.py\ntrue",
        PATCH + "bash -c 'false && python test_app.py'; true",
        PATCH + "python test_app.py & true",
        PATCH + "printf text | python test_app.py",
        PATCH + "if false; then python test_app.py; fi",
        PATCH + "(python test_app.py)",
        PATCH + 'printf "$(python test_app.py)"',
        PATCH + "eval 'python test_app.py'",
        PATCH + "python test_app.py; unknown-editor app.py",
        PATCH + "cat <<'DOC'\nignored\n DOC\npython test_app.py",
        PATCH + "printf 'unclosed\npython test_app.py",
        PATCH.replace("<<'PATCH'", "<<PATCH").replace(
            "*** End Patch", "$(python test_app.py)\n*** End Patch"
        )
        + "true",
        PATCH + "cat <<DOC\n$(python test_app.py)\nDOC\ntrue",
    ],
)
def test_compound_order_or_executable_evidence_cannot_be_assumed(command):
    result = evaluate(command)
    metric = result.metric("test_execution_rate")
    assert metric.verdict is Verdict.BAD
    assert metric.evidence["bad_spans"][0]["span_id"] == "real-mutation-span"


def test_later_action_verification_still_overrides_unprovable_compound_order():
    result = evaluate(PATCH + "printf 'pytest'", later_command="python test_app.py")
    assert result.verdict is Verdict.GOOD
    assert result.metric("test_execution_rate").evidence["verification"]["step_index"] == 1


@pytest.mark.parametrize(
    "command, runner_output",
    [
        ("python -m unittest --help", "usage: python -m unittest [-h] [-v] [-q]"),
        ("python -m unittest discover -v --help", "usage: python -m unittest discover [-h]"),
        ("pytest --version", "pytest 8.4.2"),
        ("pytest -q --version", "pytest 8.4.2"),
        ("pytest -V", "pytest 8.4.2"),
        ("pytest --collect-only -q", "2 tests collected in 0.01s"),
        ("pytest --setup-plan -q", "test_app.py::test_add (fixtures used: tmp_path)"),
        ("python -m pytest --setup-plan -q", "test_app.py::test_add (fixtures used: tmp_path)"),
        ("pytest --setup-only -q", "SETUP F tmp_path\nTEARDOWN F tmp_path"),
        ("pytest --co", "<Module test_app.py>\n<Function test_add>"),
        ("pytest --collectonly", "<Module test_app.py>\n<Function test_add>"),
        ("pytest --fixtures", "pytest fixtures available"),
        ("pytest --funcargs", "pytest fixtures available"),
        ("pytest --markers", "@pytest.mark.skip(reason): skip the test"),
        ("python test_app.py -h", "usage: test_app.py [-h] [-v]"),
        ("npm run test -- --help", "Usage: vitest [options]"),
    ],
)
def test_runner_information_with_exit_zero_does_not_prove_verification(command, runner_output):
    result = evaluate(
        PATCH + command,
        output=(
            "Process exited with code 0\nSuccess. Updated the following files:\nM app.py\n"
            + runner_output
        ),
    )
    assert result.verdict is Verdict.BAD
    assert (
        result.metric("test_execution_rate").evidence["bad_spans"][0]["span_id"]
        == "real-mutation-span"
    )


@pytest.mark.parametrize("option", ["-qh", "-vh", "-qbh", "-fch", "--h", "--he", "--hel"])
def test_real_unittest_help_clusters_and_abbreviations_do_not_verify(option):
    process = subprocess.run(
        [sys.executable, "-m", "unittest", option],
        capture_output=True,
        text=True,
        check=True,
    )
    assert process.returncode == 0
    assert "usage:" in process.stdout
    assert "Ran " not in process.stdout + process.stderr
    result = evaluate(
        PATCH + f"python -m unittest {option}",
        output=(
            "Process exited with code 0\nSuccess. Updated app.py\n"
            + process.stdout
            + process.stderr
        ),
    )
    assert result.verdict is Verdict.BAD


@pytest.mark.parametrize("option", ["--setup-plan", "--setup-only", "--collectonly", "--funcargs"])
def test_real_pytest_information_mode_does_not_run_tests_or_verify_a_repair(tmp_path, option):
    marker = tmp_path / "test-ran"
    (tmp_path / "test_repair.py").write_text(
        "from pathlib import Path\n"
        "def test_repair():\n"
        "    Path('test-ran').write_text('executed')\n"
        "    assert False, 'repair remains broken'\n"
    )
    process = subprocess.run(
        [sys.executable, "-m", "pytest", option, "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not marker.exists()
    result = evaluate(
        PATCH + f"python -m pytest {option} -q",
        output="Process exited with code 0\nSuccess. Updated app.py\n"
        + process.stdout
        + process.stderr,
    )
    assert result.verdict is Verdict.BAD


@pytest.mark.parametrize("option", ["--setup-show", "--trace-config"])
def test_real_pytest_display_mode_runs_tests_and_verifies_a_repair(tmp_path, option):
    marker = tmp_path / "test-ran"
    (tmp_path / "test_repair.py").write_text(
        "from pathlib import Path\n"
        "def test_repair():\n"
        "    Path('test-ran').write_text('executed')\n"
    )
    process = subprocess.run(
        [sys.executable, "-m", "pytest", option, "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert marker.read_text() == "executed"
    result = evaluate(
        PATCH + f"python -m pytest {option} -q",
        output="Process exited with code 0\nSuccess. Updated app.py\n"
        + process.stdout
        + process.stderr,
    )
    assert result.verdict is Verdict.GOOD


@pytest.mark.parametrize(
    "command",
    [
        "python -m unittest -khelp",
        "python -m unittest discover -phelp*.py",
        "python -m unittest --ver",
        "pytest -khelp",
        "pytest -rh",
        "pytest -vqh",
    ],
)
def test_runner_short_value_and_help_options_have_distinct_semantics(command):
    result = evaluate(PATCH + command)
    expected = Verdict.BAD if command == "pytest -vqh" else Verdict.GOOD
    assert result.verdict is expected


def test_unittest_help_flag_at_any_position_in_short_boolean_option_clusters():
    # unittest's supported booleans may be combined; -k takes the remaining
    # characters as a value and is checked separately by the positive tests.
    for size in (1, 2, 3):
        for flags in itertools.product("qvfcbh", repeat=size):
            if "h" not in flags:
                continue
            command = "python -m unittest -" + "".join(flags)
            assert evaluate(PATCH + command).verdict is Verdict.BAD, command
