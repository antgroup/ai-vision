"""Real child Python/pytest paths; these do not claim OS sandbox qualification."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[4] / "src/agentlens/diagnoser/executors/rule/custom"


def context(tmp_path, test_source, **kw):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedContext

    return EnhancedContext(
        {
            "files": [
                {"path": "tests/test_candidate.py", "encoding": "utf-8", "content": test_source}
            ]
        },
        tmp_path,
        timeout_seconds=kw.get("timeout", 10),
    )


def test_real_positive_and_negative_pytest(tmp_path):
    ctx = context(tmp_path, "from candidate import add\ndef test_add(): assert add(2,3)==5")
    good = ctx.run_python_tests("def add(a,b): return a+b", "tests/test_candidate.py")
    assert (good["verdict"], good["passed"], good["total"]) == ("GOOD", 1, 1)
    bad = ctx.run_python_tests("def add(a,b): return a-b", "tests/test_candidate.py")
    assert bad["verdict"] == "BAD" and bad["failed"] == 1
    assert bad["failures"] and "assert" in bad["failures"][0]["reason"]


@pytest.mark.parametrize(
    "candidate",
    [
        "def broken(: pass",
        "import unavailable_candidate_library",
        'raise RuntimeError("candidate boom")',
    ],
)
def test_candidate_source_and_import_faults_are_bad(tmp_path, candidate):
    ctx = context(tmp_path, "import candidate\ndef test_exists(): assert candidate is not None")
    result = ctx.run_python_tests(candidate, "tests/test_candidate.py")
    assert result["verdict"] == "BAD"
    assert result["failures"][0]["path"].endswith("candidate.py")


@pytest.mark.parametrize(
    "tests",
    [
        "def broken(: pass",
        "x=1",
        "import pytest\n@pytest.mark.skip\ndef test_skip(): pass",
        "import pytest\n@pytest.mark.xfail\ndef test_xfail(): assert False",
        "import unavailable_reference_library\ndef test_x(): pass",
        "def test_missing(unknown_fixture): pass",
    ],
)
def test_invalid_or_incomplete_reference_suite_never_passes(tmp_path, tests):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    with pytest.raises(EnhancedHarnessError):
        context(tmp_path, tests).run_python_tests("x=1", "tests/test_candidate.py")


def test_bad_retained_with_incomplete_coverage(tmp_path):
    ctx = context(
        tmp_path,
        "import pytest\ndef test_bad(): assert False\n@pytest.mark.skip\ndef test_skip(): pass",
    )
    result = ctx.run_python_tests("x=1", "tests/test_candidate.py")
    assert result["verdict"] == "BAD" and result["skipped"] == 1
    assert result["incomplete"] is True


def test_deadline_kills_pytest_process_group(tmp_path):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    ctx = context(tmp_path, "from candidate import run\ndef test_run(): run()", timeout=0.4)
    with pytest.raises(EnhancedHarnessError, match="deadline"):
        ctx.run_python_tests("def run():\n import time\n time.sleep(20)", "tests/test_candidate.py")


def test_binary_material_and_readonly_reference_inputs(tmp_path):
    import base64

    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedContext

    ctx = EnhancedContext(
        {
            "files": [
                {
                    "path": "data.bin",
                    "encoding": "base64",
                    "content": base64.b64encode(b"\x00\xff").decode(),
                }
            ],
            "expected": 3,
        },
        tmp_path,
        timeout_seconds=5,
    )
    assert ctx.read_bytes("data.bin") == b"\x00\xff"
    assert ctx.expected == 3
    assert Path(ctx.material_path("data.bin")).stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError):
        ctx.material_path("../data.bin")


@pytest.mark.parametrize(
    "entry",
    [
        'def evaluate(trace):\n import math\n print("hello worker")\n'
        ' return {"value":math.sqrt(trace["n"])}',
        "def evaluate(trace, context):\n import json\n"
        ' return {"value":json.loads(context.read_text("data.json"))["n"]}',
    ],
)
def test_worker_real_imports_entries_and_print_protocol(entry):
    bootstrap = (
        f"import sys;sys.path.insert(0,{str(MODULE_DIR)!r});"
        "import enhanced_worker;enhanced_worker.main()"
    )
    request = {
        "code": entry,
        "trace": {"n": 9},
        "timeout_seconds": 5,
        "materials": {"files": [{"path": "data.json", "encoding": "utf-8", "content": '{"n":3}'}]},
    }
    process = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    response = json.loads(process.stdout)
    assert response["status"] == "ok" and response["result"]["value"] == 3
    if "print" in entry:
        assert "hello worker" in response["captured_stdout"]


def test_worker_native_writes_and_child_output_cannot_corrupt_protocol():
    bootstrap = (
        f"import sys;sys.path.insert(0,{str(MODULE_DIR)!r});"
        "import enhanced_worker;enhanced_worker.main()"
    )
    request = {
        "code": (
            "def evaluate(trace):\n import os, subprocess, sys\n"
            ' os.write(1,b"native output\\n")\n'
            ' subprocess.run([sys.executable,"-c","print(12345)"])\n return {}'
        ),
        "trace": {},
        "timeout_seconds": 5,
    }
    process = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
    )
    response = json.loads(process.stdout)
    assert response["status"] == "ok"
    assert "native output" in response["captured_stdout"] and "12345" in response["captured_stdout"]


def test_real_child_descendant_is_killed_after_test_success(tmp_path):
    import time

    heartbeat = tmp_path / "heartbeat"
    script = (
        f"import time;from pathlib import Path;p=Path({str(heartbeat)!r});\n"
        "while True:\n p.open('a').write('x');time.sleep(0.02)"
    )
    candidate = (
        "def spawn():\n import subprocess, sys, time\n from pathlib import Path\n"
        f" child = subprocess.Popen([sys.executable,'-c',{script!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f" while not Path({str(heartbeat)!r}).exists(): time.sleep(0.01)\n"
        " return child.pid"
    )
    ctx = context(
        tmp_path / "ctx", "from candidate import spawn\ndef test_spawn(): assert spawn() > 0"
    )
    assert ctx.run_python_tests(candidate, "tests/test_candidate.py")["verdict"] == "GOOD"
    time.sleep(0.1)
    previous = heartbeat.read_bytes()
    time.sleep(0.15)
    assert heartbeat.read_bytes() == previous


@pytest.mark.parametrize(
    "tests",
    [
        "import pytest\n@pytest.fixture\ndef resource():\n yield\n"
        ' pytest.skip("incomplete teardown")\ndef test_resource(resource): pass',
        "import pytest\n@pytest.mark.xfail\ndef test_unexpected_pass(): pass",
    ],
)
def test_incomplete_teardown_or_unexpected_pass_cannot_be_good(tmp_path, tests):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    with pytest.raises(EnhancedHarnessError, match="incomplete"):
        context(tmp_path, tests).run_python_tests("x=1", "tests/test_candidate.py")


def test_explicit_reference_failure_is_bad(tmp_path):
    ctx = context(
        tmp_path, 'import pytest\ndef test_failure(): pytest.fail("candidate output incorrect")'
    )
    assert ctx.run_python_tests("x=1", "tests/test_candidate.py")["verdict"] == "BAD"


def test_internal_harness_exit_cannot_be_reported_as_candidate_bad(tmp_path, monkeypatch):
    from agentlens.diagnoser.executors.rule.custom import enhanced_context

    def broken_harness(command, *, cwd, deadline):
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "protocol": 1,
                    "exit_code": 3,
                    "passed": 0,
                    "failed": 1,
                    "total": 1,
                    "skipped": 0,
                    "errors": 0,
                    "failures": [{"path": "candidate.py", "reason": "assertion"}],
                }
            )
        )
        return 3, "internal error", ""

    monkeypatch.setattr(enhanced_context, "_run_bounded", broken_harness)
    with pytest.raises(enhanced_context.EnhancedHarnessError):
        context(tmp_path, "def test_x(): assert False").run_python_tests(
            "x=1", "tests/test_candidate.py"
        )


def test_missing_child_report_never_passes(tmp_path):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    with pytest.raises(EnhancedHarnessError, match="missing or malformed"):
        context(tmp_path, "import candidate\ndef test_x(): pass").run_python_tests(
            "import os;os._exit(0)", "tests/test_candidate.py"
        )


@pytest.mark.parametrize("other_fails", [False, True])
def test_collection_skip_is_incomplete_even_with_an_executed_case(tmp_path, other_fails):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    reference = (
        "import pytest\n"
        "class TestRequired:\n"
        " def pytest_generate_tests(self, metafunc):\n"
        '  pytest.skip("required case unavailable")\n'
        " def test_required(self, item): assert False\n"
        f"def test_other(): assert {not other_fails}\n"
    )
    ctx = context(tmp_path, reference)
    if other_fails:
        result = ctx.run_python_tests("value = 1", "tests/test_candidate.py")
        assert result["verdict"] == "BAD" and result["incomplete"] is True
        assert result["skipped"] == 1
    else:
        with pytest.raises(EnhancedHarnessError, match="incomplete"):
            ctx.run_python_tests("value = 1", "tests/test_candidate.py")


def test_pytest_output_is_live_pipe_and_preserved_below_capture_limit(tmp_path):
    ctx = context(
        tmp_path,
        "import os, stat\ndef test_capture():\n"
        " assert stat.S_ISFIFO(os.fstat(1).st_mode)\n"
        ' os.write(1, b"candidate stdout marker\\n")\n'
        ' os.write(2, b"candidate stderr marker\\n")\n',
    )
    result = ctx.run_python_tests("value = 1", "tests/test_candidate.py")
    assert result["verdict"] == "GOOD"
    assert "candidate stdout marker" in result["stdout"]
    assert "candidate stderr marker" in result["stderr"]


def test_noisy_test_output_is_bounded_before_pytest_can_capture_to_disk(tmp_path):
    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    ctx = context(tmp_path, 'import os\ndef test_noise(): os.write(1, b"x" * 100000)')
    with pytest.raises(EnhancedHarnessError, match="output exceeds capture limit"):
        ctx.run_python_tests("value = 1", "tests/test_candidate.py")


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_captured_output_survives_actual_service_protocol_parser(separator):
    from agentlens.service.sandbox_execution import _worker_result

    bootstrap = (
        f"import sys;sys.path.insert(0,{str(MODULE_DIR)!r});"
        "import enhanced_worker;enhanced_worker.main()"
    )
    text = "before" + separator + "after"
    request = {
        "code": f'def evaluate(trace):\n print({text!r})\n return {{"value":3}}',
        "trace": {},
        "timeout_seconds": 5,
    }
    process = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["captured_stdout"] == text + "\n"
    result = _worker_result(
        {
            "exit_code": 0,
            "stdout": process.stdout,
            "stderr": "",
            "timed_out": False,
            "truncated": False,
            "cleanup_verified": True,
        }
    )
    assert result["value"] == 3


def test_capture_buffer_stops_allocating_chunks_after_limit():
    from agentlens.diagnoser.executors.rule.custom.enhanced_worker import (
        MAX_CAPTURE_BYTES,
        _Capture,
    )

    output = _Capture()
    output.write("x" * MAX_CAPTURE_BYTES)
    for _ in range(10000):
        output.write("y")
    assert output.getvalue() == "x" * MAX_CAPTURE_BYTES
    assert output.truncated is True
    assert len(output.parts) == 1


def test_output_limit_kills_noisy_test_and_its_child_group(tmp_path):
    import time

    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedHarnessError

    heartbeat = tmp_path / "noisy-child-heartbeat"
    child = (
        f"import time;from pathlib import Path;p=Path({str(heartbeat)!r});\n"
        'while True:\n p.open("a").write("x");time.sleep(0.02)'
    )
    candidate = (
        "def run():\n import os, subprocess, sys, time\n from pathlib import Path\n"
        f' subprocess.Popen([sys.executable,"-c",{child!r}])\n'
        f" while not Path({str(heartbeat)!r}).exists(): time.sleep(0.01)\n"
        ' os.write(1, b"x" * 100000)\n time.sleep(20)'
    )
    ctx = context(tmp_path / "ctx", "from candidate import run\ndef test_run(): run()", timeout=5)
    with pytest.raises(EnhancedHarnessError) as error:
        ctx.run_python_tests(candidate, "tests/test_candidate.py")
    assert error.value.failure_code == "rule_output_too_large"
    time.sleep(0.1)
    stopped = heartbeat.read_bytes()
    time.sleep(0.15)
    assert heartbeat.read_bytes() == stopped


def test_context_parses_expected_json_without_changing_material_identity(tmp_path):
    import math

    from agentlens.diagnoser.executors.rule.custom.enhanced_context import EnhancedContext

    wire = ' {"number":1e-7,"negative_zero":-0.0,"text":"汉"} \n'
    ctx = EnhancedContext({"expected_json": wire}, tmp_path, timeout_seconds=5)
    assert ctx.expected["number"] == 1e-7
    assert ctx.expected["text"] == "汉"
    assert math.copysign(1, ctx.expected["negative_zero"]) == -1
    assert ctx._materials["expected_json"] == wire


def test_reference_hooks_adapt_non_python_callbacks_for_real_pluggy():
    """Cython callbacks need Python signatures/generators for pluggy registration."""
    import inspect
    from types import SimpleNamespace

    from _pytest.config import PytestPluginManager

    from agentlens.diagnoser.executors.rule.custom.enhanced_worker import _pytest_reports

    events = []
    # Bound builtins, like Cython methods, are not inspect.isfunction callbacks.
    collector = SimpleNamespace(
        pytest_collection_finish=events.append,
        pytest_collectreport=events.append,
        pytest_runtest_makereport=lambda item, call, report: events.append((item, call, report)),
    )
    assert not inspect.isfunction(collector.pytest_collection_finish)
    plugin = _pytest_reports(collector, pytest.hookimpl)
    manager = PytestPluginManager()
    manager.register(plugin)
    manager.hook.pytest_collection_finish(session="session")
    manager.hook.pytest_collectreport(report="collection")
    manager.hook.pytest_runtest_makereport(item="item", call="call")
    assert events == ["session", "collection", ("item", "call", None)]
    assert inspect.isgeneratorfunction(plugin.pytest_runtest_makereport)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc fallback")
def test_group_permission_fallback_kills_real_descendants(tmp_path, monkeypatch):
    import os
    import signal
    import time

    from agentlens.diagnoser.executors.rule.custom import enhanced_context

    real_killpg = os.killpg

    def mediated_group_signal(pid, sig):
        raise PermissionError(1, "group signals denied by provider")

    monkeypatch.setattr(enhanced_context.os, "killpg", mediated_group_signal)
    heartbeat = tmp_path / "fallback-heartbeat"
    grandchild = (
        f"import time;from pathlib import Path;p=Path({str(heartbeat)!r});\n"
        "while True: p.open('a').write('x');time.sleep(0.01)"
    )
    child = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "time.sleep(30)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", child],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert heartbeat.exists()
        enhanced_context._kill_group(parent)
        assert parent.returncode == -signal.SIGKILL
        previous = heartbeat.read_bytes()
        time.sleep(0.1)
        assert heartbeat.read_bytes() == previous
    finally:
        try:
            real_killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait(timeout=2)


def test_group_permission_fallback_never_signals_changed_identity(monkeypatch):
    from agentlens.diagnoser.executors.rule.custom import enhanced_context

    identities = iter([(1000, 42, 123, "S"), (1000, 43, 124, "S")])
    monkeypatch.setattr(enhanced_context.os, "getuid", lambda: 1000)
    monkeypatch.setattr(enhanced_context, "_proc_identity", lambda pid: next(identities))
    sent = []
    monkeypatch.setattr(enhanced_context.os, "kill", lambda *args: sent.append(args))
    monkeypatch.delattr(enhanced_context.os, "pidfd_open", raising=False)
    enhanced_context._kill_session_member(99, (1000, 42, 123, "S"))
    assert sent == []


def test_group_permission_fallback_is_linux_only(monkeypatch):
    from types import SimpleNamespace

    from agentlens.diagnoser.executors.rule.custom import enhanced_context

    def denied(*args):
        raise PermissionError(1, "group signal denied")

    monkeypatch.setattr(enhanced_context.os, "killpg", denied)
    monkeypatch.setattr(enhanced_context.sys, "platform", "darwin")
    with pytest.raises(PermissionError):
        enhanced_context._kill_group(SimpleNamespace(pid=42))


def test_group_permission_fallback_scan_bound_is_fail_closed(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from agentlens.diagnoser.executors.rule.custom import enhanced_context

    def denied(*args):
        raise PermissionError(1, "group signal denied")

    monkeypatch.setattr(enhanced_context.os, "killpg", denied)
    monkeypatch.setattr(enhanced_context.sys, "platform", "linux")
    monkeypatch.setattr(enhanced_context.os, "getuid", lambda: 1000)
    monkeypatch.setattr(enhanced_context, "_proc_identity", lambda pid: (1000, 42, 123, "S"))
    entries = [SimpleNamespace(name="not-a-pid")] * 4097
    monkeypatch.setattr(enhanced_context.os, "scandir", lambda path: nullcontext(iter(entries)))
    with pytest.raises(enhanced_context.EnhancedHarnessError, match="cleanup exceeds bound"):
        enhanced_context._kill_group(SimpleNamespace(pid=42))


@pytest.mark.parametrize("entrypoint,expected_status", [("main", "ok"), ("sandbox_main", "error")])
def test_restricted_reader_bound_is_remote_only(entrypoint, expected_status):
    bootstrap = (
        f"import sys;sys.path.insert(0,{str(MODULE_DIR)!r});"
        f"import worker_main;worker_main.{entrypoint}()"
    )
    request = {
        "code_hash": "local-compatibility",
        "code": 'def evaluate(trace): return len(trace["padding"])',
        "trace": {"padding": "x" * (16 * 1024**2)},
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == expected_status
    if expected_status == "ok":
        assert body["result"] == 16 * 1024**2
    else:
        assert body["reason"] == "worker input exceeds bound"
