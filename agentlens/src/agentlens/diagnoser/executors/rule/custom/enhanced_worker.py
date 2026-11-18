"""Full Python worker. Imported only inside the optional disposable workload."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import inspect
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

if __package__:
    from .enhanced_context import MAX_CAPTURE_BYTES, EnhancedContext, EnhancedHarnessError
    from .enhanced_contract import RUNTIME_ID, validate_enhanced_code, validate_runtime_manifest
else:
    from enhanced_context import MAX_CAPTURE_BYTES, EnhancedContext, EnhancedHarnessError
    from enhanced_contract import RUNTIME_ID, validate_enhanced_code, validate_runtime_manifest

_MANIFEST = Path("/app/rule-runtime/manifest.json")


class _Capture(io.TextIOBase):
    def __init__(self):
        self.parts = []
        self.size = 0
        self.truncated = False

    def write(self, value):
        encoded = value.encode("utf-8", errors="replace")
        remaining = MAX_CAPTURE_BYTES - self.size
        if remaining:
            self.parts.append(encoded[:remaining].decode("utf-8", errors="ignore"))
        self.size += min(len(encoded), remaining)
        self.truncated |= len(encoded) > remaining
        return len(value)

    def getvalue(self):
        return "".join(self.parts)


@contextlib.contextmanager
def _capture_fds(stdout, stderr):
    """Drain native/child writes as well as Python print, without unbounded disk IO."""
    saved, readers = [], []

    def drain(read_fd, target):
        try:
            while chunk := os.read(read_fd, 8192):
                target.write(chunk.decode("utf-8", errors="replace"))
        finally:
            os.close(read_fd)

    try:
        for descriptor, target in ((1, stdout), (2, stderr)):
            saved.append((descriptor, os.dup(descriptor)))
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, descriptor)
            os.close(write_fd)
            reader = threading.Thread(target=drain, args=(read_fd, target), daemon=True)
            reader.start()
            readers.append(reader)
        yield
    finally:
        for descriptor, original in saved:
            os.dup2(original, descriptor)
            os.close(original)
        for reader in readers:
            reader.join(timeout=0.2)


def describe_runtime() -> dict:
    """Build qualification must import the actual isolated dependency inventory."""
    return verify_environment(json.loads(_MANIFEST.read_text("utf-8")))


def verify_environment(manifest: dict) -> dict:
    manifest = validate_runtime_manifest(manifest)
    if manifest["python_version"] != ".".join(map(str, sys.version_info[:3])):
        raise EnhancedHarnessError(
            "worker Python does not match qualified runtime", "rule_runtime_unavailable"
        )
    for name, expected in manifest["packages"].items():
        try:
            if importlib.metadata.version(name) != expected:
                raise ValueError
            # Distribution names of optional transitives need not be import names.
            if name in {"pytest", "numpy", "pandas", "pydantic", "jsonschema", "openpyxl"}:
                importlib.import_module(name)
        except Exception as exc:
            raise EnhancedHarnessError(
                "advertised runtime library unavailable: " + name, "rule_runtime_unavailable"
            ) from exc
    return manifest


def _execute(request: dict) -> dict:
    compiled = validate_enhanced_code(request["code"])
    manifest = None
    if _MANIFEST.is_file():
        manifest = verify_environment(json.loads(_MANIFEST.read_text("utf-8")))
        if (
            request.get("rule_runtime") != RUNTIME_ID
            or request.get("rule_runtime_manifest") != manifest["manifest_sha256"]
        ):
            raise EnhancedHarnessError(
                "requested runtime does not match worker manifest", "rule_runtime_unavailable"
            )
    elif request.get("rule_runtime_manifest"):
        raise EnhancedHarnessError("worker runtime manifest is missing", "rule_runtime_unavailable")
    timeout = request.get("timeout_seconds", 30)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 300:
        raise ValueError("invalid execution deadline")
    with tempfile.TemporaryDirectory(prefix="aiv-rule-") as directory:
        context = EnhancedContext(request.get("materials"), directory, timeout_seconds=timeout)
        scope = {"__name__": "__enhanced_rule__"}
        # User source and imports execute here, never in admission/control-plane validation.
        exec(compiled, scope)
        evaluate = scope["evaluate"]
        count = len(inspect.signature(evaluate).parameters)
        result = evaluate(request["trace"], context) if count == 2 else evaluate(request["trace"])
        if time.monotonic() >= context._deadline:
            raise EnhancedHarnessError("enhanced execution deadline exceeded", "rule_timeout")
        return {"status": "ok", "result": result, "runtime_manifest": manifest}


def main() -> None:
    # A sandbox lease is single-use; bounded protocol reads one input only.
    line = sys.stdin.buffer.readline(16 * 1024 * 1024 + 1)
    captured_out, captured_err = _Capture(), _Capture()
    try:
        if len(line) > 16 * 1024 * 1024:
            raise ValueError("enhanced worker input exceeds bound")
        request = json.loads(line)
        with (
            _capture_fds(captured_out, captured_err),
            contextlib.redirect_stdout(captured_out),
            contextlib.redirect_stderr(captured_err),
        ):
            response = _execute(request)
    except Exception as exc:
        response = {
            "status": "error",
            "failure_code": getattr(exc, "failure_code", "rule_runtime_error"),
            "reason": str(exc)[:2000],
        }
    response.update(
        captured_stdout=captured_out.getvalue(),
        captured_stderr=captured_err.getvalue(),
        output_truncated=captured_out.truncated or captured_err.truncated,
    )
    try:
        encoded = json.dumps(response, ensure_ascii=True, allow_nan=False)
        if len(encoded.encode()) > 512 * 1024:
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        encoded = json.dumps(
            {
                "status": "error",
                "failure_code": "rule_output_too_large",
                "reason": "enhanced result is not bounded finite JSON",
            }
        )
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def _pytest_reports(collector, hookimpl):
    """Give pluggy Python functions while keeping the collector native-compiled.

    Cython methods are callable but do not satisfy pluggy's function/generator
    introspection. Only this fixed, image-owned adapter is interpreted; no
    candidate or reference source participates in constructing the hooks.
    """
    namespace = {"collector": collector, "hookimpl": hookimpl}
    exec(
        "class Reports:\n"
        "    def pytest_collection_finish(self, session):\n"
        "        return collector.pytest_collection_finish(session)\n"
        "    def pytest_collectreport(self, report):\n"
        "        return collector.pytest_collectreport(report)\n"
        "    @hookimpl(hookwrapper=True)\n"
        "    def pytest_runtest_makereport(self, item, call):\n"
        "        outcome = yield\n"
        "        collector.pytest_runtest_makereport(item, call, outcome.get_result())\n",
        namespace,
    )
    return namespace["Reports"]()


def reference_main() -> None:
    """Fixed trusted child harness; pytest report is distinct from candidate output."""
    import pytest

    reference, candidate, report_path = map(Path, sys.argv[1:])
    sys.path.insert(0, str(candidate.parent))
    counts = {
        "protocol": 1,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "total": 0,
        "errors": 0,
        "failures": [],
    }
    candidate_name = str(candidate)
    skipped_nodes, failed_nodes, passed_nodes = set(), set(), set()

    def failure(report, candidate_fault, reason=None):
        if candidate_fault:
            failed_nodes.add(report.nodeid)
            passed_nodes.discard(report.nodeid)
            counts["failed"] = len(failed_nodes)
            counts["passed"] = len(passed_nodes)
        else:
            counts["errors"] += 1
        if len(counts["failures"]) < 20:
            counts["failures"].append(
                {
                    "path": "candidate.py" if candidate_fault else reference.name,
                    "reason": (reason or str(report.longrepr))[:2000],
                    "test": report.nodeid,
                    "line": (getattr(report, "location", (None, None))[1] or 0) + 1,
                }
            )

    class Reports:
        def pytest_collection_finish(self, session):
            counts["total"] = len(session.items)

        def pytest_collectreport(self, report):
            if report.skipped:
                skipped_nodes.add(report.nodeid)
                counts["skipped"] = len(skipped_nodes)
            elif report.failed:
                failure(
                    report,
                    candidate_name in str(report.longrepr)
                    or bool(re.search(r"(?:^|\n)candidate\.py:\d+:", str(report.longrepr))),
                )

        def pytest_runtest_makereport(self, item, call, report):
            if report.skipped or hasattr(report, "wasxfail"):
                skipped_nodes.add(report.nodeid)
                passed_nodes.discard(report.nodeid)
                counts["skipped"] = len(skipped_nodes)
                counts["passed"] = len(passed_nodes)
            elif report.failed:
                candidate_fault = (
                    report.when == "call"
                    and call.excinfo is not None
                    and call.excinfo.errisinstance((AssertionError, pytest.fail.Exception))
                )
                if call.excinfo is not None:
                    candidate_fault |= any(
                        str(entry.path) == candidate_name for entry in call.excinfo.traceback
                    )
                failure(report, candidate_fault)
            elif report.when == "call" and report.passed:
                passed_nodes.add(report.nodeid)
                counts["passed"] = len(passed_nodes)

    exit_code = int(
        pytest.main(
            [
                str(reference),
                "-q",
                "--capture=no",
                "-c",
                "/dev/null",
                "--noconftest",
                "--import-mode=importlib",
                "-p",
                "no:cacheprovider",
                "--tb=short",
            ],
            plugins=[_pytest_reports(Reports(), pytest.hookimpl)],
        )
    )
    counts["exit_code"] = exit_code
    report_path.write_text(json.dumps(counts, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(exit_code)
