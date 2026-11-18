"""Material access and bounded, fixed-argv reference testing inside one workload."""

from __future__ import annotations

import ast
import errno
import importlib.util
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from .enhanced_contract import (
        MAX_CODE_BYTES,
        material_bytes,
        normalize_materials,
        validate_material_path,
    )
else:
    from enhanced_contract import (
        MAX_CODE_BYTES,
        material_bytes,
        normalize_materials,
        validate_material_path,
    )

MAX_CAPTURE_BYTES = 32 * 1024
MAX_REPORT_BYTES = 128 * 1024
_RUNTIME_DIR = Path("/app/rule-runtime")


class EnhancedHarnessError(RuntimeError):
    def __init__(self, message: str, failure_code: str = "rule_test_harness_error"):
        super().__init__(message)
        self.failure_code = failure_code


def child_command() -> list[str]:
    """Only image-owned paths in deployment; source-tree fallback is for source QA."""
    if Path(__file__).parent == _RUNTIME_DIR:
        interpreter = "/usr/local/bin/python3.11"
        paths = [str(_RUNTIME_DIR), str(_RUNTIME_DIR / "site-packages")]
    else:
        interpreter = sys.executable
        spec = importlib.util.find_spec("pytest")
        if spec is None or spec.origin is None:
            raise EnhancedHarnessError(
                "advertised pytest runtime is unavailable", "rule_runtime_unavailable"
            )
        paths = [str(Path(__file__).parent), str(Path(spec.origin).parent.parent)]
    bootstrap = (
        "import sys;sys.path[:0]="
        + repr(paths)
        + ";import enhanced_worker;enhanced_worker.reference_main()"
    )
    return [interpreter, "-I", "-S", "-B", "-c", bootstrap]


def _proc_identity(pid: int):
    try:
        with Path(f"/proc/{pid}/stat").open("rb") as stream:
            owner = os.fstat(stream.fileno()).st_uid
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise EnhancedHarnessError("process identity exceeds cleanup bound")
        fields = raw.rsplit(b")", 1)[1].split()
        return owner, int(fields[3]), int(fields[19]), fields[0].decode("ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None


def _kill_session_member(pid: int, identity: tuple) -> None:
    if pid <= 1 or pid == os.getpid() or identity[0] != os.getuid():
        return
    current = _proc_identity(pid)
    if current is None or current[:3] != identity[:3]:
        return
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(pid, 0)
            except OSError as error:
                if error.errno != errno.ENOSYS:
                    raise
        current = _proc_identity(pid)
        if current is None or current[:3] != identity[:3]:
            return
        if descriptor is not None:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                return
            except OSError as error:
                if error.errno != errno.ENOSYS:
                    raise
        # OpenShell mediates positive PIDs but may not implement pidfd/group
        # signals. This identity check is not an atomic adversarial PID fence;
        # independent whole-sandbox teardown remains the success authority.
        current = _proc_identity(pid)
        if current is not None and current[:3] == identity[:3]:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _kill_session(process: subprocess.Popen, identity, deadline: float) -> None:
    if identity is None:
        identity = _proc_identity(process.pid)
    if identity is None or identity[0] != os.getuid() or identity[1] != process.pid:
        raise EnhancedHarnessError("owned process session cannot be verified")
    while time.monotonic() < deadline:
        leader = _proc_identity(process.pid)
        if leader is not None and (
            leader[1:3] != identity[1:3]
            or (leader[3] not in ("Z", "X") and leader[0] != identity[0])
        ):
            raise EnhancedHarnessError("owned process session identity changed")
        live = []
        with os.scandir("/proc") as entries:
            for count, entry in enumerate(entries):
                if count >= 4096 or time.monotonic() >= deadline:
                    raise EnhancedHarnessError("process cleanup exceeds bound")
                if not entry.name.isdecimal():
                    continue
                pid = int(entry.name)
                if pid <= 1 or pid == os.getpid():
                    continue
                member = _proc_identity(pid)
                if (
                    member is not None
                    and member[0] == identity[0]
                    and member[1] == process.pid
                    and member[3] not in ("Z", "X")
                ):
                    live.append((pid, member))
        if not live:
            return
        for pid, member in live:
            _kill_session_member(pid, member)
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    raise EnhancedHarnessError("owned process session cleanup not verified")


def _kill_group(process: subprocess.Popen, session_identity=None) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as error:
        if sys.platform != "linux" or error.errno != errno.EPERM:
            raise
        deadline = time.monotonic() + 2
        try:
            _kill_session(process, session_identity, deadline)
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as failure:
            raise EnhancedHarnessError("owned process session cleanup failed") from failure
        return
    process.wait(timeout=2)


def _run_bounded(command: list[str], *, cwd: Path, deadline: float) -> tuple[int, str, str]:
    if time.monotonic() >= deadline:
        raise EnhancedHarnessError("reference test deadline exceeded", "rule_timeout")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
    }
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = [bytearray(), bytearray()]
    session_identity = None
    try:
        if sys.platform == "linux":
            try:
                session_identity = _proc_identity(process.pid)
            except OSError:
                pass  # Normal group cleanup does not require /proc access.
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((process.stdout, process.stderr)):
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EnhancedHarnessError("reference test deadline exceeded", "rule_timeout")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(buffers[0]) + len(buffers[1]) + len(chunk) > MAX_CAPTURE_BYTES:
                        raise EnhancedHarnessError(
                            "reference test output exceeds capture limit", "rule_output_too_large"
                        )
                    buffers[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EnhancedHarnessError("reference test deadline exceeded", "rule_timeout")
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise EnhancedHarnessError(
                    "reference test deadline exceeded", "rule_timeout"
                ) from None
        return returncode, *(b.decode("utf-8", errors="replace") for b in buffers)
    finally:
        # Descendants must die even when pytest itself exits successfully.
        try:
            _kill_group(process, session_identity)
        finally:
            process.stdout.close()
            process.stderr.close()


class EnhancedContext:
    def __init__(self, materials: dict | None, root: str | Path, *, timeout_seconds: float):
        self._materials = normalize_materials(materials)
        self.expected = (
            json.loads(self._materials["expected_json"])
            if "expected_json" in self._materials
            else self._materials.get("expected")
        )
        self._root = Path(root)
        self._reference = self._root / "materials"
        self._work = self._root / "work"
        self._reference.mkdir(mode=0o700, parents=True)
        self._work.mkdir(mode=0o700, parents=True)
        self._paths = set()
        self._deadline = time.monotonic() + timeout_seconds
        self._runs = 0
        for item in self._materials["files"]:
            path = self._reference / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(material_bytes(item))
            path.chmod(0o444)
            self._paths.add(item["path"])
        for directory in sorted(self._reference.rglob("*"), reverse=True):
            if directory.is_dir():
                directory.chmod(0o555)
        self._reference.chmod(0o555)

    @property
    def work_dir(self) -> str:
        return str(self._work)

    def material_path(self, path: str) -> str:
        validate_material_path(path)
        if path not in self._paths:
            raise ValueError("reference material was not supplied: " + path)
        target = self._reference / path
        if (
            target.is_symlink()
            or not target.is_file()
            or not target.resolve().is_relative_to(self._reference.resolve())
        ):
            raise EnhancedHarnessError("reference material identity changed")
        return str(target)

    def read_bytes(self, path: str) -> bytes:
        return Path(self.material_path(path)).read_bytes()

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8")

    def run_python_tests(self, code: str, tests_path: str) -> dict:
        started = time.monotonic()
        reference = Path(self.material_path(tests_path))
        if reference.suffix != ".py":
            raise EnhancedHarnessError("reference tests must name a supplied Python file")
        try:
            ast.parse(reference.read_text("utf-8"), filename=tests_path)
        except (SyntaxError, UnicodeError) as exc:
            raise EnhancedHarnessError("invalid reference test source: " + str(exc)[:1000]) from exc
        if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("candidate source exceeds code limit")
        self._runs += 1
        run_dir = self._work / ("run-" + str(self._runs))
        run_dir.mkdir(mode=0o700)
        candidate = run_dir / "candidate.py"
        candidate.write_text(code, encoding="utf-8")
        try:
            compile(code, str(candidate), "exec")
        except SyntaxError as exc:
            return {
                "verdict": "BAD",
                "passed": 0,
                "failed": 1,
                "total": 0,
                "skipped": 0,
                "incomplete": True,
                "failures": [
                    {"path": "candidate.py", "line": exc.lineno, "reason": str(exc)[:2000]}
                ],
                "stdout": "",
                "stderr": "",
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        report = run_dir / "report.json"
        command = child_command() + [str(reference), str(candidate), str(report)]
        returncode, stdout, stderr = _run_bounded(command, cwd=run_dir, deadline=self._deadline)
        try:
            with report.open("rb") as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or result.get("protocol") != 1
                or result.get("exit_code") != returncode
            ):
                raise ValueError
            for key in ("passed", "failed", "total", "skipped", "errors"):
                if type(result.get(key)) is not int or result[key] < 0:
                    raise ValueError
            if (
                not isinstance(result.get("failures"), list)
                or len(result["failures"]) > 20
                or (result["failed"] and not result["failures"])
            ):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise EnhancedHarnessError("missing or malformed reference test report") from None
        if result["errors"] or returncode not in (0, 1, 2, 5):
            raise EnhancedHarnessError(
                "reference test harness failed: " + str(result["failures"])[:2000]
            )
        incomplete = bool(
            result["skipped"]
            or result["total"] == 0
            or result["passed"] + result["failed"] < result["total"]
        )
        if not result["failed"] and (incomplete or returncode != 0):
            raise EnhancedHarnessError(
                "reference verification incomplete: no tests, skipped/xfail tests, "
                "or incomplete report",
                "rule_test_incomplete",
            )
        result.pop("protocol")
        result.pop("errors")
        result.update(
            verdict="BAD" if result["failed"] else "GOOD",
            incomplete=incomplete,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result
