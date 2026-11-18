"""Isolated official Codex CLI driver using vetted production cdxhooks."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from urllib.parse import urlsplit

from codex_provider import loopback
from docker_loopback import docker_loopback
from models import AgentResult, CaseContext


@dataclass(frozen=True)
class CodexContext(CaseContext):
    hook_binary: Path | None = None
    cancel_ready: threading.Event | None = None


def local_docker(docker=None, endpoint=None):
    docker = str(docker or shutil.which("docker") or "")
    if not docker:
        raise ValueError("Docker CLI is required for the native Linux runtime")
    endpoint = endpoint or os.environ.get("DOCKER_HOST")
    if not endpoint:
        result = subprocess.run([docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            capture_output=True, text=True, timeout=10)
        try: endpoint = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError: endpoint = None
    parsed = urlsplit(endpoint or "")
    if parsed.scheme != "unix" or parsed.netloc or not parsed.path.startswith("/") or parsed.query or parsed.fragment:
        raise ValueError("native Codex container requires an explicit local Docker socket")
    return docker, endpoint


def codex_container_runtime(image, repo, out, workspace, *, docker=None, endpoint=None):
    """Host networking retains the harness's loopback-only contract.

    The Docker socket is explicit so the isolated HOME needs no personal
    Docker context. Source mounts are read-only; only case artifacts and the
    prepared fixture workspace are writable. The image contains official
    @openai/codex 0.161.0 and the same Linux libc as the hook build image.
    """
    docker, endpoint = local_docker(docker, endpoint)
    if not image:
        raise ValueError("native Codex container requires an explicit local Docker socket and image")
    for path in (repo, out, workspace):
        if not Path(path).is_absolute(): raise ValueError("container paths must be absolute")
    return (str(docker), "-H", endpoint, "run", "--rm", "--network=host",
        "--name=aiv-codex-e2e-" + uuid.uuid4().hex,
        "--user=" + str(os.getuid()) + ":" + str(os.getgid()),
        "--volume", str(repo) + ":" + str(repo) + ":ro",
        "--volume", str(out) + ":" + str(out) + ":rw",
        "--volume", str(workspace) + ":" + str(workspace) + ":rw",
        "--workdir", str(workspace), "--entrypoint", "/usr/bin/env", str(image),
        "/usr/local/bin/node", "/opt/codex/node_modules/@openai/codex/bin/codex.js")


def _container_lifecycle(context):
    """Keep the native SessionEnd worker alive until its durable queue drains."""
    path = context.out / "codex-container-lifecycle.cjs"
    path.write_text('''const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const child = spawn(process.argv[2], process.argv.slice(3), {stdio:"inherit"});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
function files(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, {withFileTypes:true}).flatMap(entry => {
    const name = path.join(root, entry.name);
    return entry.isDirectory() ? files(name) : [name];
  });
}
function pending() {
  for (const name of files(process.env.CDXHOOKS_STATE_DIR)) {
    if (path.basename(name) === "state_stop_intent.json") return true;
    if (path.basename(name) === "worker.queue.jsonl") {
      const offset = path.join(path.dirname(name), "worker.queue.offset.json");
      const position = fs.existsSync(offset) ? JSON.parse(fs.readFileSync(offset, "utf8")) : 0;
      if (fs.statSync(name).size > position) return true;
    }
  }
  return files(process.env.CDXHOOKS_SPOOL_DIR).some(name =>
    /[\\/]((pending)|(claimed))[\\/].+\\.json$/.test(name));
}
child.on("error", error => {console.error(error.message); process.exit(127);});
child.on("exit", async (code, sig) => {
  const deadline = Date.now() + 10000;
  await new Promise(resolve => setTimeout(resolve, 250));
  while (Date.now() < deadline) {
    try {if (!pending()) break;} catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  process.exit(code === null ? (sig === "SIGINT" ? 130 : 143) : code);
});
''', encoding="utf-8")
    return path


def _command(context, env, arguments, executable=None, *, wait_hooks=False):
    runtime = list(context.runtime)
    if "--entrypoint" in runtime and runtime[runtime.index("--entrypoint") + 1] == "/usr/bin/env":
        image = runtime.index("--entrypoint") + 2
        # The host still needs its PATH to launch Docker, but the Linux child
        # must retain the image's tool paths instead of receiving a Mac PATH.
        inherited = [argument for key in env if key != "PATH" for argument in ("--env", key)]
        command = [str(executable)] if executable else runtime[image + 1:]
        if wait_hooks:
            command = ["/usr/local/bin/node", str(_container_lifecycle(context))] + command
        return runtime[:image] + inherited + [runtime[image]] + command + arguments
    return ([str(executable)] if executable else runtime) + arguments


def _remove_container(argv, env):
    name = next((argument.split("=", 1)[1] for argument in argv if argument.startswith("--name=aiv-codex-e2e-")), None)
    if name and "run" in argv:
        subprocess.run(argv[:argv.index("run")] + ["rm", "--force", name], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


def _hook_binary(context):
    explicit = getattr(context, "hook_binary", None)
    suffix = ".exe" if os.name == "nt" else ""
    candidates = ([Path(explicit)] if explicit else
        [Path(argument).parent / ("cdxhooks" + suffix) for argument in context.runtime] + [
            context.repo / "ai-vision/agent-plugins/codex/cdxhooks/target/release" / ("cdxhooks" + suffix),
            context.repo / "target/release" / ("cdxhooks" + suffix)])
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError("build vetted production common,tool_context cdxhooks before native Codex diagnostics")


def _kill_group(process, sig):
    if os.name == "posix":
        # SIGKILL cannot be caught. Once delivered to our group, repeating it
        # can race group teardown (EPERM on Darwin) or signal a reused PGID.
        # Leader exit alone is insufficient: descendants may still own pipes.
        if getattr(process, "_owned_group_terminated", False):
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process._owned_group_terminated = True
        else:
            if sig == signal.SIGKILL:
                process._owned_group_terminated = True
        # Genuine EPERM is intentionally propagated, never marked terminated.
    elif process.poll() is None:
        process.kill()


def _interrupt_native(process, argv, env):
    name = next((argument.split("=", 1)[1] for argument in argv if argument.startswith("--name=aiv-codex-e2e-")), None)
    if name and "run" in argv:
        result = subprocess.run(argv[:argv.index("run")] + ["kill", "--signal", "SIGINT", name],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if result.returncode:
            raise RuntimeError("native container SIGINT failed: " + result.stderr.strip())
    else:
        process.send_signal(signal.SIGINT)


def _invoke(argv, env, workspace, timeout, *, cancellation=None, cancel_ready=None):
    process = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        start_new_session=os.name == "posix")
    rows = queue.Queue()
    def read(pipe, target):
        try:
            for line in pipe: rows.put((target, line))
        finally: pipe.close()
    readers = [threading.Thread(target=read, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for reader in readers: reader.start()
    output = [[], []]
    deadline, timed_out = time.monotonic() + timeout, False
    interrupt_at = None
    try:
        while process.poll() is None or any(reader.is_alive() for reader in readers) or not rows.empty():
            if time.monotonic() >= deadline:
                timed_out = True
                # Descendants can keep inherited pipes alive after the parent
                # exits; the process group remains ours and must be stopped.
                _kill_group(process, signal.SIGKILL)
                break
            if (cancellation is not None and process.poll() is None and interrupt_at is not None
                    and not cancellation.get("sent_ns") and not cancellation.get("signal_error")
                    and (cancel_ready.is_set() if cancel_ready is not None else time.monotonic() >= interrupt_at)):
                cancellation["sent_ns"] = time.time_ns()
                try:
                    _interrupt_native(process, argv, env)
                    cancellation["signal_sent"] = True
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    cancellation["signal_error"] = str(error)
            try:
                index, line = rows.get(timeout=min(.05, max(.001, deadline-time.monotonic())))
                output[index].append(line)
                if cancellation is not None and index == 0:
                    events = _events(line)
                    if any(row.get("type") == "turn.started" for row in events):
                        cancellation["turn_started_ns"] = time.time_ns()
                        interrupt_at = time.monotonic() + 1
                    if any(row.get("type") in {"turn.completed", "turn.failed"} for row in events):
                        interrupt_at = None
            except queue.Empty: pass
        if process.poll() is None: process.wait(timeout=1)
    finally:
        active_error = sys.exception()
        cleanup_errors = []
        try:
            _kill_group(process, signal.SIGKILL)
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            _remove_container(argv, env)
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            if process.poll() is None:
                process.wait(timeout=1)
        except BaseException as error:
            cleanup_errors.append(error)
        for reader in readers:
            try:
                reader.join(timeout=.3)
            except BaseException as error:
                cleanup_errors.append(error)
        while not rows.empty():
            index, line = rows.get_nowait()
            output[index].append(line)
        if cleanup_errors:
            if active_error is not None:
                cleanup_errors.insert(0, active_error)
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise BaseExceptionGroup("Native Codex invocation and cleanup failed", cleanup_errors)
    return (124 if timed_out else process.returncode), "".join(output[0]), "".join(output[1]), timed_out


def _events(output):
    result = []
    for line in output.splitlines():
        try: row = json.loads(line)
        except ValueError: continue
        if isinstance(row, dict): result.append(row)
    return result


def _transcript(home, session_id):
    if not session_id: return None, "", []
    # The native host owns these files; the driver only discovers and reads
    # them. Never fabricate a session file from exec stdout.
    for path in sorted((home / "sessions").rglob("*.jsonl")):
        if path.stat().st_size > 16 * 1024 * 1024: continue
        text = path.read_text(encoding="utf-8")
        rows = _events(text)
        if any(row.get("type") == "session_meta" and row.get("payload", {}).get("id") == session_id for row in rows):
            return path, text, rows
    return None, "", []


def _wait_hooks(state, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = list(state.rglob("pending/*.json")) + list(state.rglob("claimed/*.json"))
        pending += list(state.rglob("state_stop_intent.json"))
        for path in state.rglob("worker.queue.jsonl"):
            try:
                offset_path = path.with_name("worker.queue.offset.json")
                offset = json.loads(offset_path.read_text()) if offset_path.exists() else 0
                if path.stat().st_size > offset: pending.append(path)
            except (OSError, ValueError, TypeError): pending.append(path)
        if not pending: return True
        time.sleep(min(.1, max(0, deadline-time.monotonic())))
    return False


def run_case(context: CaseContext) -> AgentResult:
    if "--network=host" not in context.runtime:
        return _run_case(context)
    with docker_loopback(context.provider_url, context.collector_url) as endpoints:
        return _run_case(replace(context, provider_url=endpoints[0], collector_url=endpoints[1]))


def _run_case(context: CaseContext) -> AgentResult:
    if context.mode not in {"fake", "nvidia"}:
        raise ValueError("native Codex diagnostic mode must be fake or nvidia")
    provider, collector = loopback(context.provider_url), loopback(context.collector_url)
    if not provider.endswith("/v1"): provider += "/v1"
    if not context.runtime or not 0 < context.timeout_seconds <= 1800:
        raise ValueError("native Codex requires runtime and bounded positive timeout")
    hook = _hook_binary(context)
    manifest = context.repo / "ai-vision/agent-plugins/codex/cdxhooks/Cargo.toml"
    expected_hook = tomllib.loads(manifest.read_text())["package"]["version"]
    home, codex_home = context.out / "home", context.out / "home/.codex"
    for path in (home, codex_home, context.out / "temp", context.workspace): path.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR") if key in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(codex_home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
        "TMPDIR": str(context.out / "temp"), "TMP": str(context.out / "temp"), "TEMP": str(context.out / "temp"),
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
        "NATIVE_E2E_LOOPBACK_KEY": "native-e2e-placeholder", "CDXHOOKS_ENABLE_TELEMETRY": "true",
        "CDXHOOKS_COLLECTION_MODE": "privacy" if context.scenario == "privacy" else "full",
        "CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": collector + "/v1/traces",
        "CDXHOOKS_METRICS_ENDPOINT": collector + "/v1/metrics", "CDXHOOKS_OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": collector + "/v1/logs",
        "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS": json.dumps(context.headers),
        "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES": ",".join(key + "=" + value for key, value in context.resource.items()),
        "CDXHOOKS_STATE_DIR": str(context.out / "hook-state"), "CDXHOOKS_SPOOL_DIR": str(context.out / "hook-spool"),
        "CDXHOOKS_LOG_DIR": str(context.out / "hook-logs"), "CDXHOOKS_DEBUG": "true",
        "CDXHOOKS_REALTIME_EXPORT": "true"})
    if "ant.username" in context.resource: env["LANGFUSE_ATTR_USERNAME"] = context.resource["ant.username"]
    # These are our own vetted source binary and hook definitions. No account,
    # real HOME, installed skills, user rules or global hook config are loaded.
    config = '\n'.join([
        "model = " + json.dumps(context.model), 'model_provider = "native-e2e"',
        'approval_policy = "never"', 'sandbox_mode = "danger-full-access"',
        'web_search = "disabled"', 'model_reasoning_effort = "low"',
        '[features]', 'codex_hooks = true', 'multi_agent = false', 'apps = false',
        '[model_providers.native-e2e]', 'name = "Native diagnostic loopback"',
        'base_url = ' + json.dumps(provider), 'wire_api = "responses"',
        'env_key = "NATIVE_E2E_LOOPBACK_KEY"', 'requires_openai_auth = false',
        'supports_websockets = false', 'request_max_retries = 1', 'stream_max_retries = 0',
        'stream_idle_timeout_ms = ' + str(int(context.timeout_seconds * 1000)), ""])
    (codex_home / "config.toml").write_text(config, encoding="utf-8")
    command = shlex.quote(str(hook))
    hook_config = {"hooks": {event: [{"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": 15}]}]
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")}}
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(json.dumps(hook_config), encoding="utf-8")
    provenance = {"binary": str(hook), "binary_sha256": hashlib.sha256(hook.read_bytes()).hexdigest(),
        "manifest": str(manifest), "source_version": expected_hook, "config_path": str(hooks_path),
        "config_sha256": hashlib.sha256(hooks_path.read_bytes()).hexdigest(), "config": hook_config,
        "trust": "explicit invocation of vetted production hook config",
        "collection_mode": env["CDXHOOKS_COLLECTION_MODE"]}
    code, version_out, version_err, timeout = _invoke(_command(context, env, ["--version"]), env, context.workspace, min(30, context.timeout_seconds))
    match = re.search(r"(?:codex(?:-cli)?\s+)?(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)", version_out)
    version = match.group(1) if match else ""
    expected = context.runtime_version or "0.161.0"
    if code or version != expected:
        return AgentResult(code or 1, None, version, version_out, {"phase": "version", "stderr": version_err,
            "error": "native Codex requires runtime version " + expected, "hook_config_provenance": provenance})
    code, hook_out, hook_err, _ = _invoke(_command(context, env, ["--version"], hook), env, context.workspace, min(30, context.timeout_seconds))
    if code or not re.search(r"\b" + re.escape(expected_hook) + r"\b", hook_out):
        return AgentResult(code or 1, None, version, hook_out, {"phase": "hook-version", "stderr": hook_err,
            "error": "cdxhooks must match source version " + expected_hook, "hook_config_provenance": provenance})
    code, hook_info, hook_err, _ = _invoke(_command(context, env, ["info"], hook), env, context.workspace, min(30, context.timeout_seconds))
    try: info = json.loads(hook_info)
    except ValueError: info = {}
    # The production info command names the key-free common edition "default".
    if code or info.get("edition") != "default" or info.get("version") != expected_hook:
        return AgentResult(code or 1, None, version, hook_info, {"phase": "hook-edition", "stderr": hook_err,
            "error": "native diagnostics require current common cdxhooks without embedded keys", "hook_config_provenance": provenance})
    provenance["build_info"] = info
    outputs, errors, cli_events, exit_codes = [], [], [], []
    session_id = None
    turns_completed = 0
    timed_out = False
    cancellation = {"requested": context.scenario == "cancel", "signal": "SIGINT",
        "trigger": "upstream_request_started" if getattr(context, "cancel_ready", None) is not None else "native_turn_started_plus_1s",
        "sent_ns": None, "signal_sent": False, "native_abort_observed": False}
    code = 0
    for prompt in (context.prompt, context.followup_prompt):
        if not prompt: continue
        arguments = ["exec", "--json", "--color", "never", "--ignore-rules",
                "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--skip-git-repo-check"]
        if session_id: arguments += ["resume", session_id]
        arguments += [prompt]
        argv = _command(context, env, arguments, wait_hooks=True)
        code, output, stderr, timeout = _invoke(argv, env, context.workspace, context.timeout_seconds,
            cancellation=cancellation if cancellation["requested"] else None,
            cancel_ready=getattr(context, "cancel_ready", None))
        outputs.append(output)
        errors.append(stderr)
        exit_codes.append(code)
        timed_out |= timeout
        events = _events(output)
        cli_events.extend(events)
        observed = [row.get("thread_id") for row in events if row.get("type") == "thread.started"]
        if observed:
            if len(set(observed)) != 1 or (session_id and observed[0] != session_id):
                code = 1
                errors.append("native Codex session changed unexpectedly")
                break
            session_id = observed[0]
        completed = sum(row.get("type") == "turn.completed" for row in events)
        turns_completed += completed
        if code or timeout: break
        if completed != 1 or not session_id:
            code = 1
            errors.append("native Codex did not emit one completed turn and session id")
            break
    drained = _wait_hooks(context.out / "hook-state", min(10, context.timeout_seconds))
    drained &= _wait_hooks(context.out / "hook-spool", min(10, context.timeout_seconds))
    transcript, transcript_text, native_rows = _transcript(codex_home, session_id)
    cancellation["native_abort_observed"] = any(row.get("type") == "event_msg"
        and row.get("payload", {}).get("type") == "turn_aborted"
        and row.get("payload", {}).get("reason") == "interrupted" for row in native_rows)
    cancelled = (cancellation["requested"] and cancellation["signal_sent"]
        and cancellation["native_abort_observed"] and not timed_out and turns_completed == 0 and code != 0)
    if not code and (not transcript or not drained):
        code = 1
        errors.append("native transcript missing" if not transcript else "production hook queue did not drain")
    tools = [row["item"] for row in cli_events if row.get("type") == "item.completed" and row.get("item", {}).get("type") in {"command_execution", "file_change", "mcp_tool_call"}]
    finals = [row["item"]["text"] for row in cli_events if row.get("type") == "item.completed" and row.get("item", {}).get("type") == "agent_message"]
    return AgentResult(code, session_id, version, "".join(outputs), {"stderr": "".join(errors),
        "cli_events": cli_events, "turns_completed": turns_completed, "process_exit_codes": exit_codes,
        "timed_out": timed_out, "cancelled": cancelled, "cancellation": cancellation, "hook_config_provenance": provenance,
        "transcript_path": str(transcript) if transcript else None, "native_transcript": native_rows,
        "native_transcript_text": transcript_text, "native_transcript_sha256": hashlib.sha256(transcript_text.encode()).hexdigest() if transcript else None,
        "tool_outcomes": tools, "final_answers": finals, "hook_queue_drained": drained,
        "hook_log_paths": [str(path) for path in sorted((context.out / "hook-logs").glob("*.log"))]})
