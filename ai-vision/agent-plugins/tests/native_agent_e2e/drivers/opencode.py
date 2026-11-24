"""Official OpenCode CLI driver for isolated native acceptance cases."""
from __future__ import annotations

import codecs
import io
import json
from contextlib import contextmanager
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from models import AgentResult, CaseContext
from drivers._process import _launch


def _loopback(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("native OpenCode endpoints must use an HTTP loopback service")
    return url.rstrip("/")


def _group_alive(pid: int) -> bool:
    # Darwin can report EPERM while an empty group is being retired. Only skip
    # groups without live members; permission errors for live groups propagate.
    members = subprocess.run(["ps", "-eo", "pgid=,stat="], capture_output=True,
                             text=True, check=True, timeout=.5).stdout.splitlines()
    return any(len(row := line.split()) == 2 and row[0] == str(pid)
               and not row[1].startswith("Z") for line in members)


def _stop(process: subprocess.Popen, interrupt: bool = False, job=None, grace_seconds: float = 3) -> None:
    if os.name == "posix":
        parent_alive = process.poll() is None
        if not parent_alive and not _group_alive(process.pid):
            return
        try:
            os.killpg(process.pid, (signal.SIGINT if interrupt else signal.SIGTERM)
                      if parent_alive else signal.SIGKILL)
        except ProcessLookupError:
            pass
        if parent_alive:
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass
            if process.poll() is None or _group_alive(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    elif job is not None:
        job.close()
    elif process.poll() is None:
        process.terminate()
    process.wait(timeout=.5)


def _invoke(argv: list[str], env: dict[str, str], workspace: Path, timeout: int, cancel: bool = False, abort=None):
    process, job = _launch(argv, cwd=workspace, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
    lines: queue.Queue[str] = queue.Queue()
    stop_reader = threading.Event()
    read_errors: list[OSError] = []
    def read():
        assert process.stdout is not None
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), translate=True)
        pending = ""
        try:
            # Python 3.12 supports nonblocking Windows pipes as well as POSIX.
            os.set_blocking(process.stdout.fileno(), False)
            while not stop_reader.is_set():
                try:
                    chunk = os.read(process.stdout.fileno(), 65536)
                except BlockingIOError:
                    stop_reader.wait(.01)
                    continue
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    lines.put(line + "\n")
            pending += decoder.decode(b"", final=True)
            if pending:
                lines.put(pending)
        except OSError as error:
            read_errors.append(error)
        finally:
            # This reader alone owns close: no concurrent blocking readline.
            process.stdout.close()
    reader = threading.Thread(target=read, daemon=True, name="opencode-stdout")
    reader.start()
    output: list[str] = []
    timed_out = cancelled = False
    live_at_timeout = False
    deadline = time.monotonic() + timeout
    stopped = False
    try:
        while process.poll() is None or reader.is_alive() or not lines.empty():
            # Cancellation may already have reported expiry. The reader's
            # stop state independently guards the bounded drain transition.
            if not stop_reader.is_set() and time.monotonic() >= deadline:
                timed_out = True
                if not stopped:
                    live_at_timeout = process.poll() is None
                    stopped = True
                    _stop(process, job=job, grace_seconds=.5)
                reader.join(timeout=.5)
                stop_reader.set()
            try:
                line = lines.get(timeout=.1)
            except queue.Empty:
                continue
            output.append(line)
            if cancel and not cancelled:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if event.get("type") in {"step_start", "text"}:
                    if abort and isinstance(event.get("sessionID"), str):
                        cancelled = abort(event["sessionID"])
                    else:
                        cancelled = True
                        stopped = True
                        _stop(process, interrupt=True, job=job,
                              grace_seconds=min(3, max(0, deadline - time.monotonic())))
                        timed_out |= time.monotonic() >= deadline
        process.wait(timeout=3)
        if read_errors:
            raise read_errors[0]
    finally:
        try:
            if not stopped:
                _stop(process, job=job)
        finally:
            stop_reader.set()
            reader.join(timeout=.5)
    code = process.returncode
    # Windows Job cleanup can yield zero. Preserve the genuine status when
    # the parent already exited and an inherited pipe caused the timeout.
    if live_at_timeout and code == 0:
        code = 1
    return code, "".join(output), timed_out, cancelled


@contextmanager
def _attached_server(runtime, env, workspace, timeout, enabled):
    if not enabled:
        yield None
        return
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server, job = _launch([*runtime, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=workspace, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    opener = build_opener(ProxyHandler({}))
    try:
        deadline = time.monotonic() + min(timeout, 60)
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with opener.open(url + "/global/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, ValueError):
                time.sleep(.1)
        else:
            raise RuntimeError("isolated official OpenCode server did not become ready")
        yield url
    finally:
        _stop(server, job=job)


def _abort_session(server_url, workspace, session_id):
    request = Request(server_url + "/session/" + quote(session_id, safe="") + "/abort?" + urlencode({"directory": str(workspace)}),
                      data=b"", method="POST")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
            return response.status == 200 and json.loads(response.read()) is True
    except (OSError, ValueError):
        return False


def _wait_generated_title(server_url, workspace, session_id, timeout):
    """Wait for the official title task's persisted completion, never idle alone."""
    opener = build_opener(ProxyHandler({}))
    url = server_url + "/session/" + quote(session_id, safe="") + "?" + urlencode({"directory": str(workspace)})
    deadline = time.monotonic() + timeout
    # Exact default title format in official OpenCode 1.18.34 session.ts.
    default = re.compile(r"^(?:New session|Child session) - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=min(2, max(.1, deadline - time.monotonic()))) as response:
                raw = response.read(128 * 1024 + 1)
                info = json.loads(raw) if response.status == 200 and len(raw) <= 128 * 1024 else {}
                title = info.get("title") if isinstance(info, dict) else None
                if isinstance(info, dict) and info.get("id") == session_id and isinstance(title, str) and title.strip() and not default.fullmatch(title):
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    return False


def _wait_completed_message(server_url, workspace, session_id, model, provider_id, previous_ids, timeout):
    """Attach CLI may return before stdout emits stop; inspect official state."""
    opener = build_opener(ProxyHandler({}))
    url = server_url + "/session/" + quote(session_id, safe="") + "/message?" + urlencode({"directory": str(workspace), "limit": 32})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=min(2, max(.1, deadline - time.monotonic()))) as response:
                raw = response.read(128 * 1024 + 1)
                rows = json.loads(raw) if response.status == 200 and len(raw) <= 128 * 1024 else []
            infos = [row.get("info", {}) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            infos = [info for info in infos if isinstance(info, dict) and info.get("sessionID") == session_id]
            users = [info for info in infos if info.get("role") == "user" and isinstance(info.get("id"), str)
                     and type(info.get("time", {}).get("created")) is int]
            if users:
                user = max(users, key=lambda info: info["time"]["created"])
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("info"), dict):
                        continue
                    info = row["info"]
                    completed = info.get("time", {}).get("completed")
                    if (info.get("sessionID") == session_id and info.get("role") == "assistant" and info.get("parentID") == user["id"]
                            and info.get("modelID") == model and info.get("providerID") == provider_id
                            and info.get("finish") == "stop" and type(completed) is int and completed > 0
                            and isinstance(info.get("id"), str) and info["id"] not in previous_ids):
                        parts = row.get("parts", [])
                        text = "\n".join(part["text"] for part in parts if isinstance(part, dict)
                            and part.get("type") == "text" and isinstance(part.get("text"), str)) if isinstance(parts, list) else ""
                        return {"source": "official-session-api", "message_id": info["id"],
                            "parent_message_id": user["id"], "session_id": session_id,
                            "model": model, "provider_id": provider_id, "text": text}
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    return None


def _dependency_seed(runtime) -> Path:
    """Locate the installer's complete official package tree without npm calls."""
    for argument in runtime:
        executable = Path(argument)
        if not executable.is_file():
            continue
        for parent in list(executable.resolve().parents)[:4]:
            seed = parent / "opencode-config-seed"
            if not seed.is_dir():
                continue
            try:
                manifest = json.loads((seed / "package.json").read_text())
                lock = json.loads((seed / "package-lock.json").read_text())
                declared = manifest["dependencies"]["@opencode-ai/plugin"]
                locked = lock["packages"][""]["dependencies"]["@opencode-ai/plugin"]
                versions = [json.loads((seed / "node_modules/@opencode-ai" / name / "package.json").read_text())["version"]
                            for name in ("plugin", "sdk")]
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError("Official OpenCode dependency seed is incomplete") from None
            if declared != "1.18.34" or locked != declared or versions != [declared, declared]:
                raise ValueError("Official OpenCode dependency seed requires plugin and SDK 1.18.34")
            return seed
    raise ValueError("Install the official OpenCode dependency seed before running native cases")


def run_case(context: CaseContext) -> AgentResult:
    if context.mode not in {"fake", "nvidia"}:
        raise ValueError("native OpenCode mode must be fake or nvidia")
    provider = _loopback(context.provider_url)
    collector = _loopback(context.collector_url)
    provider = provider if provider.endswith("/v1") else provider + "/v1"
    collector = collector.removesuffix("/v1")
    if not context.runtime or not 0 < context.timeout_seconds <= 1800:
        raise ValueError("runtime and bounded positive timeout_seconds are required")
    plugin = context.repo / "ai-vision/agent-plugins/opencode/observ-opencode/dist/index.js"
    if not plugin.is_file():
        raise ValueError("build the production observ-opencode plugin before running native cases")
    seed = _dependency_seed(context.runtime)
    home = context.out / "home"
    config_dir = home / ".config/opencode"
    for path in (home, config_dir, home / ".local/share", home / ".cache", home / ".local/state", context.out / "temp"):
        path.mkdir(parents=True, exist_ok=True)
    # The host awaits npm installation even for a local production plugin.
    # Stage the installer's actual pinned packages and lock into this case's
    # private config directory so native startup needs no registry access.
    shutil.copytree(seed / "node_modules", config_dir / "node_modules")
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(seed / name, config_dir / name)
    provider_id = "nvidia" if context.mode == "nvidia" else "native-e2e"
    selected = provider_id + "/" + context.model
    config = {
        "$schema": "https://opencode.ai/config.json", "plugin": [plugin.resolve().as_uri()],
        "model": selected, "small_model": selected, "enabled_providers": [provider_id],
        "share": "disabled", "permission": {"*": "allow"},
        "provider": {provider_id: {"npm": "@ai-sdk/openai-compatible", "name": "Native E2E loopback",
            "options": {"baseURL": provider, "apiKey": "native-e2e-placeholder", "timeout": context.timeout_seconds * 1000},
            "models": {context.model: {"name": context.model, "limit": {"context": 32768, "output": 4096}, "tool_call": True}}}},
        "agent": {"build": {"steps": 8}},
    }
    telemetry = {"enableTelemetry": True, "collectionMode": "privacy" if context.scenario == "privacy" else "full",
        "otelExporter": {"endpoint": collector, "protocol": "http/protobuf", "resourceAttributes": context.resource,
            "metricsTemporalityPreference": "delta",
            "attributes": {key: value for key, value in context.resource.items() if key in {"ant.username", "ant.uid"}}},
        "metricExportInterval": 1000, "logsExportInterval": 1000,
        "logDir": str(context.out / "plugin-logs")}
    for signal_name, config_name in (("traces", "tracesConfig"), ("logs", "eventsConfig"), ("metrics", "metricsConfig")):
        telemetry[config_name] = {"endpoint": collector + "/v1/" + signal_name, "enabled": True}
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (config_dir / "observ-opencode.json").write_text(json.dumps(telemetry), encoding="utf-8")
    # Only OS process necessities survive. Provider/OTLP credentials and host
    # configuration from the user's shell are deliberately not inherited.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR") if key in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
        # The native runtime's Global.home uses os.homedir() rather than HOME
        # on some platforms. Its supported override and external-skill flag
        # keep system-home discovery outside the isolated acceptance process.
        "OPENCODE_TEST_HOME": str(home), "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
        "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
        "TEMP": str(context.out / "temp"), "TMP": str(context.out / "temp"), "TMPDIR": str(context.out / "temp"),
        "OPENCODE_CONFIG": str(config_path), "OPENCODE_CONFIG_DIR": str(config_dir),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true", "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true",
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
        "OTEL_EXPORTER_OTLP_HEADERS": ",".join(key + "=" + value for key, value in context.headers.items())})
    version_code, version_output, _, _ = _invoke([*context.runtime, "--version"], env, context.workspace, min(context.timeout_seconds, 30))
    version = next((line.strip().removeprefix("v") for line in reversed(version_output.splitlines()) if re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+].*)?", line.strip())), "")
    if version_code or not version:
        return AgentResult(version_code or 1, None, version, version_output, {"phase": "version"})
    outputs: list[str] = []
    session_id = None
    timed_out = cancelled = False
    code = 0
    turns_completed = 0
    resumed_session_id = None
    title_completed = None
    completed_message_ids = []
    final_answers = []
    failure_phase = None
    # The one-shot CLI can exit before asynchronous plugin export completes.
    # Keep the official server alive through native completion and its normal
    # exporter drain for fake cases as well as live and cancellation cases.
    attach_enabled = True
    with _attached_server(context.runtime, env, context.workspace, context.timeout_seconds, attach_enabled) as attached:
        try:
            for prompt in (context.prompt, context.followup_prompt):
                if not prompt:
                    continue
                argv = [*context.runtime, "run", "--format", "json", "--model", selected]
                if attached:
                    argv += ["--attach", attached, "--dir", str(context.workspace)]
                if session_id:
                    resumed_session_id = session_id
                    argv += ["--session", session_id]
                argv += [prompt]
                code, output, timeout, was_cancelled = _invoke(argv, env, context.workspace, context.timeout_seconds, context.scenario == "cancel",
                    (lambda sid: _abort_session(attached, context.workspace, sid)) if attached else None)
                outputs.append(output)
                timed_out |= timeout
                cancelled |= was_cancelled
                completed = False
                for line in output.splitlines():
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(event, dict) and isinstance(event.get("sessionID"), str):
                        observed = event["sessionID"]
                        if session_id and observed != session_id:
                            return AgentResult(1, session_id, version, "".join(outputs), {"error": "unexpected session switch"})
                        session_id = observed
                        completed |= event.get("type") == "step_finish" and event.get("part", {}).get("reason") == "stop"
                if code or timed_out or cancelled:
                    break
                if attached and session_id:
                    answer = _wait_completed_message(attached, context.workspace, session_id, context.model,
                        provider_id, completed_message_ids, min(context.timeout_seconds, 10))
                    if answer is None:
                        timed_out = True
                        code = 1
                        failure_phase = "background_completion"
                        break
                    completed_message_ids.append(answer["message_id"])
                    final_answers.append(answer)
                    completed = True
                if completed:
                    turns_completed += 1
                if attached and session_id and title_completed is None:
                    title_completed = _wait_generated_title(attached, context.workspace, session_id, context.timeout_seconds)
                    if not title_completed:
                        timed_out = True
                        code = 1
                        failure_phase = "background_title"
                        break
        finally:
            if session_id and not timed_out:
                # Normal completion and an acknowledged native abort can both
                # end before asynchronous metric export. Retain the same 2s
                # span batch/1s metric window before server teardown, including
                # early returns after an observed session. Timeouts keep their
                # existing bounded cleanup instead of extending the deadline.
                time.sleep(2.25)
    output = "".join(outputs)
    answers = "\n".join(answer["text"] for answer in final_answers)
    if timed_out and code == 0:
        code = 1
    # Native output stays in memory; the caller owns credential-aware evidence
    # sanitization before it persists logs from live forwarding cases.
    return AgentResult(code, session_id, version, answers if context.mode == "nvidia" else output + ("\n" + answers if answers else ""), {"timed_out": timed_out, "cancelled": cancelled,
        "provider_id": provider_id, "model": context.model, "config_dir": str(config_dir), "native_cli": True,
        "native_attach": attach_enabled, "title_completed": title_completed,
        "completed_message_ids": completed_message_ids,
        **({"final_answers": final_answers, "native_output": output} if context.mode == "nvidia" or final_answers else {}),
        **({"phase": failure_phase} if failure_phase else {}),
        "resumed_session_id": resumed_session_id, "turns_completed": turns_completed, "native_abort": context.scenario == "cancel" and cancelled})
