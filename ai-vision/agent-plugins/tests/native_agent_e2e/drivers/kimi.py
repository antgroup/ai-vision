"""Run official Kimi Code with its production Stop/Interrupt hook binary."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from models import AgentResult, CaseContext
from drivers._process import _WINDOWS_BOOTSTRAP, _launch


def _loopback(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Kimi native E2E endpoints must be HTTP loopback services")
    return value.rstrip("/")


def _hook_binary(ctx: CaseContext) -> Path:
    name = "kimihooks.exe" if os.name == "nt" else "kimihooks"
    # The installer stages the source-built production binary next to the
    # native host (or its npm entry script), without using a personal install.
    for item in reversed(ctx.runtime):
        entry = Path(item)
        if entry.is_file() and (entry.parent / name).is_file():
            return entry.parent / name
    raise ValueError("Install the production kimihooks binary next to the official Kimi runtime")


def _hook_command(hook: Path) -> list[str]:
    return [str(hook)]


def _environment(ctx: CaseContext, home: Path, profile: Path) -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    headers = json.dumps(ctx.headers)
    env.update(HOME=str(home), USERPROFILE=str(home), KIMI_CODE_HOME=str(profile),
        APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
        KIMI_DISABLE_TELEMETRY="1", KIMI_CODE_NO_AUTO_UPDATE="1", TERM="dumb",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
        AIVISION_MANAGED_INGEST="1", KIMI_COLLECTION_MODE="full", KIMI_ENABLE_TELEMETRY="true",
        AIVISION_ANT_USERNAME=ctx.resource.get("ant.username", "native-e2e"),
        AIVISION_ANT_UID=ctx.resource.get("ant.uid", "native-e2e"),
        AIVISION_AGENT_PRODUCT=ctx.resource.get("ant.agent.product", "KimiCLI"),
        AIVISION_AGENT_TYPE=ctx.resource.get("ant.agent.type", "kimicli"),
        AIVISION_AGENT_INSTANCE=ctx.resource.get("ant.agent.instance", "native-e2e"),
        KIMI_LOG_DIR=str(ctx.out / "kimihooks-logs"), KIMI_STATE_DIR=str(ctx.out / "kimihooks-state"),
        KIMI_OTLP_HEADERS=headers, KIMI_OTLP_METRICS_HEADERS=headers, KIMI_OTLP_LOGS_HEADERS=headers,
        KIMI_OTLP_TRACES_ENDPOINT=ctx.collector_url.rstrip("/") + "/v1/traces",
        KIMI_OTLP_METRICS_ENDPOINT=ctx.collector_url.rstrip("/") + "/v1/metrics",
        KIMI_OTLP_LOGS_ENDPOINT=ctx.collector_url.rstrip("/") + "/v1/logs")
    for surface in ("SPANS", "EVENTS"):
        for content in ("USER_PROMPTS", "MODEL_OUTPUTS", "TOOL_DETAILS", "TOOL_OUTPUTS"):
            env[f"KIMI_LOG_{surface}_{content}"] = "true"
    return env


def _json_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _native_sessions(profile: Path, workspace: Path) -> list[tuple[dict, Path]]:
    rows = []
    for path in sorted((profile / "sessions").glob("*/*/state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(state, dict) and state.get("id") == path.parent.name
                    and Path(state.get("cwd", "")).resolve() == workspace.resolve()):
                rows.append((state, path.parent))
        except (OSError, ValueError, TypeError):
            continue
    return rows


# Closing a TextIOWrapper while another thread is blocked in readline can
# itself block. Readers own/close their streams; cleanup never waits forever.
PIPE_DRAIN_SECONDS = .5
TREE_WAIT_SECONDS = .5


def _kill_tree(process, job):
    if os.name == "posix":
        # Timeout and finally both clean up. Repeating SIGKILL can race group
        # teardown on Darwin or signal a reused PGID after the leader exits.
        if not getattr(process, "_owned_group_terminated", False):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Genuine permission errors propagate and never mark completion.
            process._owned_group_terminated = True
    elif job is not None:
        job.close()  # kills descendants even after the original PID has exited
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=TREE_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _join_readers(readers):
    end = time.monotonic() + PIPE_DRAIN_SECONDS
    for reader in readers:
        reader.join(timeout=max(0, end - time.monotonic()))


def _invoke(command: list[str], ctx: CaseContext, env: dict[str, str], deadline: float, profile: Path):
    process, job = _launch(command, cwd=ctx.workspace, env=env, text=True, encoding="utf-8",
        errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output: queue.Queue[tuple[str, str]] = queue.Queue()
    readers = []
    for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        def read(stream=stream, label=label):
            try:
                for line in stream:
                    output.put((label, line))
            finally:
                stream.close()
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        readers.append(thread)
    stdout, stderr = [], []
    timed_out = False
    drain_deadline = None
    try:
        while process.poll() is None or any(reader.is_alive() for reader in readers) or not output.empty():
            now = time.monotonic()
            if process.poll() is not None and drain_deadline is None:
                drain_deadline = min(deadline, now + PIPE_DRAIN_SECONDS)
            if now >= deadline or drain_deadline is not None and now >= drain_deadline:
                timed_out = True
                _kill_tree(process, job)
                break
            try:
                label, line = output.get(timeout=min(.05, max(0, deadline - now)))
                (stdout if label == "stdout" else stderr).append(line)
            except queue.Empty:
                pass
    finally:
        _kill_tree(process, job)
        _join_readers(readers)
        # Only drain the finite snapshot. An escaped descendant must not extend
        # the timeout by continuously writing into a daemon reader's queue.
        for _ in range(output.qsize()):
            try:
                label, line = output.get_nowait()
                (stdout if label == "stdout" else stderr).append(line)
            except queue.Empty:
                break
    return 124 if timed_out else process.returncode, "".join(stdout), "".join(stderr), False


def _cancel_acp(ctx: CaseContext, env: dict[str, str], deadline: float, skills: Path, capture: Path):
    process, job = _launch([*ctx.runtime, "--skills-dir", str(skills), "acp"], cwd=ctx.workspace,
        env=env, text=True, encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending: queue.Queue[str | None] = queue.Queue()
    output, stderr = [], []
    def read_stdout():
        try:
            for line in process.stdout:
                pending.put(line)
        finally:
            process.stdout.close()
            pending.put(None)
    def read_stderr():
        try:
            for line in process.stderr:
                stderr.append(line)
        finally:
            process.stderr.close()
    readers = [threading.Thread(target=fn, daemon=True) for fn in (read_stdout, read_stderr)]
    for reader in readers:
        reader.start()
    def send(method, params, identity=None):
        row = {"jsonrpc":"2.0", "method":method, "params":params}
        if identity is not None:
            row["id"] = identity
        process.stdin.write(json.dumps(row) + "\n")
        process.stdin.flush()
    def until(predicate):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Kimi ACP response exceeded the native case timeout")
            line = pending.get(timeout=remaining)
            if line is None:
                raise ValueError("Kimi ACP closed before its native response")
            output.append(line)
            row = json.loads(line)
            if predicate(row):
                if row.get("error"):
                    raise ValueError("Kimi ACP rejected a native request")
                return row
    requested = cancelled = False
    code = 1
    try:
        send("initialize", {"protocolVersion":1, "clientCapabilities":{},
            "clientInfo":{"name":"native-e2e", "version":"1"}}, 1)
        until(lambda row: row.get("id") == 1)
        send("session/new", {"cwd":str(ctx.workspace), "mcpServers":[]}, 2)
        session_id = until(lambda row: row.get("id") == 2).get("result", {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Kimi ACP returned no native session identity")
        send("session/prompt", {"sessionId":session_id,
            "prompt":[{"type":"text", "text":ctx.prompt}]}, 3)
        def native_delta(row):
            params = row.get("params", {})
            update = params.get("update", {})
            content = update.get("content", {})
            return (row.get("method") == "session/update" and params.get("sessionId") == session_id
                and update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text"
                and isinstance(content.get("text"), str) and bool(content["text"]))
        until(native_delta)
        send("session/cancel", {"sessionId":session_id})
        requested = True
        cancelled = until(lambda row: row.get("id") == 3).get("result", {}).get("stopReason") == "cancelled"
        # Interrupt dispatch is asynchronous in the official host. Keep the
        # server alive until its real production hook exits before shutdown.
        while cancelled and time.monotonic() < deadline:
            if any(row.get("hook_event_name") == "Interrupt" and row.get("session_id") == session_id
                   for row in _json_lines(capture)) and _json_lines(capture.with_name("hook-dispatches.jsonl")):
                break
            time.sleep(.05)
        process.stdin.close()
        code = process.wait(timeout=min(10, max(.1, deadline - time.monotonic())))
    except (TimeoutError, queue.Empty, subprocess.TimeoutExpired):
        code = 124
    except (OSError, ValueError):
        code = process.poll() or 1
    finally:
        _kill_tree(process, job)
        if not process.stdin.closed:
            process.stdin.close()
        _join_readers(readers)
        for _ in range(pending.qsize()):
            line = pending.get_nowait()
            if line is not None:
                output.append(line)
    return code, "".join(output), "".join(stderr), requested, cancelled


def _write_configuration(ctx: CaseContext, profile: Path, empty_skills: Path, hook: Path) -> Path:
    capture = ctx.out / "native-hooks.jsonl"
    wrapper = ctx.out / "dispatch-production-hook.py"
    wrapper.write_text('''import hashlib, json, subprocess, sys
from pathlib import Path
payload = sys.stdin.buffer.read()
with Path(sys.argv[1]).open("ab") as target:
    target.write(payload.rstrip(b"\\r\\n") + b"\\n")
result = subprocess.run(sys.argv[2:], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
with Path(sys.argv[1]).with_name("hook-dispatches.jsonl").open("a", encoding="utf-8") as target:
    target.write(json.dumps({"stdin_text":payload.decode("utf-8"), "stdin_sha256":hashlib.sha256(payload).hexdigest(), "exit_code":result.returncode}) + "\\n")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
''', encoding="utf-8")
    argv = [sys.executable, str(wrapper), str(capture), *_hook_command(hook)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    config = f'''default_model = {json.dumps(ctx.model)}
telemetry = false
auto_session_title = false
builtin_product_skills = false
merge_all_available_skills = false
[providers.local]
type = "openai"
base_url = {json.dumps(ctx.provider_url)}
api_key = "local-fake-key"
[models.{json.dumps(ctx.model)}]
provider = "local"
model = {json.dumps(ctx.model)}
max_context_size = 65536
capabilities = ["tool_use"]
[loop_control]
max_attempts_per_step = 2
reserved_context_size = 4096
'''
    for event in ("Stop", "Interrupt"):
        config += f'\n[[hooks]]\nevent = "{event}"\ncommand = {json.dumps(command)}\ntimeout = 30\n'
    (profile / "config.toml").write_text(config, encoding="utf-8")
    empty_skills.mkdir()
    return capture


def _hook_version(repo: Path) -> str:
    import tomllib
    manifest = repo / "ai-vision/agent-plugins/kimi/kimihooks/Cargo.toml"
    return tomllib.loads(manifest.read_text(encoding="utf-8"))["package"]["version"]


def run_case(ctx: CaseContext) -> AgentResult:
    if ctx.mode != "fake":
        raise ValueError("Kimi Code native acceptance currently uses fake models")
    _loopback(ctx.provider_url)
    _loopback(ctx.collector_url)
    if ctx.scenario == "multi-turn" and not ctx.followup_prompt:
        raise ValueError("Kimi multi-turn requires a follow-up prompt")
    hook = _hook_binary(ctx)
    ctx.out.mkdir(parents=True, exist_ok=True)
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    home, profile = ctx.out / "kimi-home", ctx.out / "kimi-profile"
    home.mkdir(exist_ok=False)
    profile.mkdir(exist_ok=False)
    skills = ctx.out / "empty-skills"
    capture = _write_configuration(ctx, profile, skills, hook)
    env = _environment(ctx, home, profile)
    deadline = time.monotonic() + ctx.timeout_seconds
    expected = ctx.runtime_version or "2.1.1"
    version_run = subprocess.run([*ctx.runtime, "--version"], cwd=ctx.workspace, env=env,
        text=True, capture_output=True, timeout=min(30, ctx.timeout_seconds), encoding="utf-8")
    version = version_run.stdout.strip()
    details = {"runtime":"kimi", "plugin_loader":"official-native-hooks", "mode":ctx.mode,
        "model":ctx.model, "session_ids":[], "turns_completed":0, "processes":0,
        "cancel_requested":False, "cancelled":False, "process_exit_codes":[], "cli_events":[],
        "hook_payloads":[], "hook_dispatches":[], "kimi_wire":[], "native_session_state":{}, "stderr":""}
    if version_run.returncode or version != expected:
        return AgentResult(version_run.returncode or 1, None, version, version_run.stdout + version_run.stderr,
            {**details, "error":f"Kimi native acceptance requires official Kimi Code {expected}"})
    hook_run = subprocess.run([*_hook_command(hook), "--version"], cwd=ctx.workspace, env=env,
        text=True, capture_output=True, timeout=min(10, ctx.timeout_seconds), encoding="utf-8")
    expected_hook = _hook_version(ctx.repo)
    if hook_run.returncode or hook_run.stdout.strip() != "kimihooks " + expected_hook:
        return AgentResult(hook_run.returncode or 1, None, version, hook_run.stderr,
            {**details, "error":"Could not verify production kimihooks binary"})
    details["production_hook_version"] = hook_run.stdout.strip().split()[-1]
    details["production_hook_sha256"] = hashlib.sha256(hook.read_bytes()).hexdigest()
    output, session_id, exit_code = [], None, 0
    prompts = [ctx.prompt] + ([ctx.followup_prompt] if ctx.scenario == "multi-turn" else [])
    for index, prompt in enumerate(prompts):
        command = [*ctx.runtime, "--skills-dir", str(skills), "--output-format", "stream-json"]
        if index:
            command += ["-r", session_id]
        command += ["-p", prompt]
        if ctx.scenario == "cancel":
            code, stdout, stderr, requested, protocol_cancelled = _cancel_acp(ctx, env, deadline, skills, capture)
        else:
            code, stdout, stderr, requested = _invoke(command, ctx, env, deadline, profile)
        details["processes"] += 1
        details["process_exit_codes"].append(code)
        details["cancel_requested"] |= requested
        details["stderr"] += stderr
        output.append(stdout)
        events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
        details["cli_events"].extend(events)
        hooks = _json_lines(capture)
        details["hook_payloads"] = hooks
        details["hook_dispatches"] = _json_lines(capture.with_name("hook-dispatches.jsonl"))
        states = _native_sessions(profile, ctx.workspace)
        if len(states) != 1:
            exit_code = code or 1
            details["error"] = "Kimi returned no unique native session state for the isolated workspace"
            break
        state, directory = states[0]
        actual_id = state["id"]
        hints = [row.get("session_id") for row in events if row.get("type") == "session.resume_hint"]
        if session_id is not None and actual_id != session_id:
            exit_code = code or 1
            details["error"] = "Kimi resumed a different native session"
            break
        session_id = actual_id
        if index:
            details["resumed_session_id"] = session_id
        details["session_ids"].append(session_id)
        details["native_session_state"] = state
        wire_path = directory / "agents/main/wire.jsonl"
        raw = wire_path.read_bytes()
        details["kimi_wire"] = _json_lines(wire_path)
        details["kimi_wire_text"] = raw.decode("utf-8")
        details["kimi_wire_sha256"] = hashlib.sha256(raw).hexdigest()
        details["native_wire_file"] = str(wire_path)
        terminal = "Interrupt" if ctx.scenario == "cancel" else "Stop"
        matching = [row for row in hooks if row.get("session_id") == session_id
            and row.get("hook_event_name") == terminal and Path(row.get("cwd", "")).resolve() == ctx.workspace.resolve()
            and row.get("client_type") == "kimi_code_cli"]
        dispatches = details["hook_dispatches"]
        dispatch_completed = len(dispatches) == index + 1 and all(row.get("exit_code") == 0 for row in dispatches)
        if ctx.scenario == "cancel":
            cancelled = requested and protocol_cancelled and code == 0 and dispatch_completed and bool(matching) and state.get("lastTurnReason") == "cancelled"
            details["cancelled"] = cancelled
            details["cancellation_protocol"] = "official-acp"
            exit_code = 0 if cancelled else code or 1
            if not cancelled:
                details["error"] = "Kimi cancellation lacks native Interrupt and cancelled session evidence"
        else:
            completed = (code == 0 and dispatch_completed and hints == [session_id] and state.get("lastTurnReason") == "completed"
                and len(matching) == index + 1)
            if completed:
                details["turns_completed"] += 1
            else:
                exit_code = code or 1
                details["error"] = "Kimi did not complete through its native session and Stop hook"
        if exit_code:
            break
    return AgentResult(exit_code, session_id, version, "".join(output), details)
