"""Run the official Hermes headless CLI with its production directory plugin."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

try:
    from ..models import AgentResult, CaseContext
    from ._process import _join_readers, _kill_tree, _launch as _launch_owned, PIPE_DRAIN_SECONDS
except ImportError:
    from models import AgentResult, CaseContext
    from drivers._process import _join_readers, _kill_tree, _launch as _launch_owned, PIPE_DRAIN_SECONDS


def _loopback(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _environment(ctx: CaseContext, home: Path) -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), HERMES_HOME=str(home),
               PYTHONUNBUFFERED="1", NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
               OPENAI_API_KEY="local-fake-key", NVIDIA_API_KEY="local-fake-key", NVIDIA_BASE_URL=ctx.provider_url,
               AIVISION_ANT_USERNAME=ctx.resource.get("ant.username", "native-e2e"),
               AIVISION_ANT_UID=ctx.resource.get("ant.uid", "native-e2e"),
               OTEL_EXPORTER_OTLP_HEADERS=",".join(f"{key}={value}" for key, value in ctx.headers.items()))
    return env


def _session(output: str) -> str | None:
    values = re.findall(r"(?:session_id:|Session:\s*|Session ID:)\s*([A-Za-z0-9_-]+)", output)
    return values[-1] if values else None


def _raw_callbacks(directory: Path) -> list[dict]:
    """Copy the separate, explicitly enabled recorder's native hook evidence.

    The independent source oracle validates the envelopes and hash chains;
    this boundary neither invokes callbacks nor normalizes their contents.
    """
    return [json.loads(line) for path in sorted(directory.glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _launch(command: list[str], ctx: CaseContext, env: dict[str, str], deadline: float):
    remaining = max(.1, deadline - time.monotonic())
    try:
        completed = subprocess.run(command, cwd=ctx.workspace, env=env, text=True,
                                   capture_output=True, timeout=remaining, encoding="utf-8")
        return completed.returncode, completed.stdout + completed.stderr, False
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"") + (error.stderr or b"")
        return 124, output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output, False


def _cancel_acp(ctx: CaseContext, env: dict[str, str], deadline: float):
    """Use the host's stdio protocol, not OS-specific interrupt emulation."""
    process, job = _launch_owned([*ctx.runtime, "acp"], cwd=ctx.workspace, env=env, text=True,
        encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending: queue.Queue = queue.Queue()
    output: list[str] = []
    def read(stream, protocol):
        try:
            for line in stream:
                if protocol:
                    pending.put(line)
                else:
                    output.append(line)
        finally:
            # The reader owns the stream: another thread's close can block on
            # its buffered read lock while a descendant keeps the pipe open.
            stream.close()
            if protocol:
                pending.put(None)
    readers = [threading.Thread(target=read, args=(stream, protocol), daemon=True)
               for stream, protocol in ((process.stdout, True), (process.stderr, False))]
    for reader in readers:
        reader.start()
    def send(method, params, identity=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if identity is not None:
            message["id"] = identity
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
    def until(predicate):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            line = pending.get(timeout=remaining)
            if line is None:
                raise ValueError("ACP ended before its response")
            output.append(line)
            row = json.loads(line)
            if predicate(row):
                if row.get("error"):
                    raise ValueError("ACP rejected the request")
                return row
    session_id = None
    requested = False
    cancelled = False
    code = 1
    try:
        send("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                            "clientInfo": {"name": "native-e2e", "version": "1"}}, 1)
        until(lambda row: row.get("id") == 1)
        send("session/new", {"cwd": str(ctx.workspace), "mcpServers": []}, 2)
        session_id = until(lambda row: row.get("id") == 2).get("result", {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            session_id = None
            raise ValueError("ACP returned no session")
        send("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": ctx.prompt}]}, 3)
        def is_delta(row):
            params = row.get("params", {})
            update = params.get("update", {})
            content = update.get("content", {})
            return (row.get("method") == "session/update" and params.get("sessionId") == session_id
                    and update.get("sessionUpdate") == "agent_message_chunk"
                    and content.get("type") == "text" and bool(content.get("text")))
        until(is_delta)
        send("session/cancel", {"sessionId": session_id})
        requested = True
        stopped = until(lambda row: row.get("id") == 3)
        cancelled = stopped.get("result", {}).get("stopReason") == "cancelled"
        process.stdin.close()
        code = process.wait(timeout=min(10, max(.1, deadline - time.monotonic())))
        _join_readers(readers, min(PIPE_DRAIN_SECONDS, max(0, deadline - time.monotonic())))
        if any(reader.is_alive() for reader in readers):
            code = 124
    except (TimeoutError, queue.Empty, subprocess.TimeoutExpired):
        code = 124
    except (OSError, ValueError):
        code = process.poll() or 1
    finally:
        try:
            # Descendants still belong to this launch after the ACP host exits.
            _kill_tree(process, job)
        finally:
            try:
                if not process.stdin.closed:
                    process.stdin.close()
            except BrokenPipeError:
                pass
            finally:
                _join_readers(readers)
    return code or (0 if cancelled else 1), "".join(output), session_id, requested, cancelled and code == 0


def _prepare(ctx: CaseContext, *, compression: bool = False):
    """Install isolated plugins/config through the official directory boundary."""
    if ctx.mode not in ("fake", "nvidia"):
        raise ValueError("Hermes mode must be fake or nvidia")
    if not _loopback(ctx.provider_url) or not _loopback(ctx.collector_url):
        raise ValueError("Hermes acceptance requires loopback provider forwarding and OTLP capture")
    if ctx.scenario == "multi-turn" and not ctx.followup_prompt:
        raise ValueError("Hermes multi-turn requires a follow-up prompt")
    if any("," in str(value) or "\n" in str(value) for pair in ctx.headers.items() for value in pair):
        raise ValueError("Hermes OTLP headers must be unambiguous environment pairs")
    ctx.out.mkdir(parents=True, exist_ok=True)
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    home = ctx.out / "hermes-home"
    home.mkdir(exist_ok=False)
    plugin = ctx.repo / "ai-vision/agent-plugins/hermes/observ_hermes"
    shutil.copytree(plugin, home / "plugins/observ-hermes", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    capture_native = ctx.mode == "fake" and ctx.runtime_version == "0.21.5"
    if capture_native:
        replay = ctx.repo / "ai-vision/agent-plugins/hermes/hermes-replay/hermes_replay"
        shutil.copytree(replay, home / "plugins/hermes-replay", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # Developer evidence is opt-in only in this private E2E home. The
        # production plugin and the user's own configuration are independent.
        (home / "hermes-replay.json").write_text(json.dumps({"captureEnabled": True,
            "collectionMode": "full", "sessionDir": str(ctx.out / "raw-callbacks")}) + "\n", encoding="utf-8")
    streaming = ctx.scenario in ("stream", "cancel")
    provider = "nvidia" if ctx.mode == "nvidia" else "custom"
    config = {"model": {"default": ctx.model, "provider": provider, "base_url": ctx.provider_url, "max_tokens": 512},
              "plugins": {"enabled": ["observ-hermes"] + (["hermes-replay"] if capture_native else [])}, "compression": {"enabled": False},
              # This private file-tool fixture must not download optional
              # providers or the terminal-only scanner during Agent startup.
              "security": {"allow_lazy_installs": False, "tirith_enabled": False},
              "memory": {"memory_enabled": False, "user_profile_enabled": False},
              "agent": {"api_max_retries": 1}, "display": {"streaming": streaming, "final_response_markdown": "raw"}}
    if compression:
        # Public host settings make a short fixture compactable. The separate
        # compression driver enables automatic compaction on the resumed host.
        config["compression"].update(protect_first_n=0, protect_last_n=3)
        config["platform_toolsets"] = {"acp": ["file"]}
    # JSON is valid YAML. No credentials are persisted in either configuration.
    (home / "config.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    observer = {"enableTelemetry": True, "collectionMode": "full", "logDir": str(ctx.out / "hermes-plugin-logs"),
                "otelExporter": {"resourceAttributes": ctx.resource, "metricsTemporalityPreference": "delta"},
                **{name: {"enabled": True, "endpoint": ctx.collector_url.rstrip("/") + "/v1/" + target}
                   for name, target in (("tracesConfig", "traces"), ("metricsConfig", "metrics"), ("eventsConfig", "logs"))}}
    (home / "observ-hermes.json").write_text(json.dumps(observer, indent=2) + "\n", encoding="utf-8")
    env = _environment(ctx, home)
    return home, env, capture_native, provider


def run_case(ctx: CaseContext, *, additional_prompts: tuple[str, ...] = ()) -> AgentResult:
    home, env, capture_native, provider = _prepare(ctx)
    deadline = time.monotonic() + ctx.timeout_seconds
    code, version_output, _ = _launch([*ctx.runtime, "--version"], ctx, env, deadline)
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?![\d.])", version_output)
    version = match.group(1) if match else "unknown"
    details = {"plugin_loader": "official-directory", "plugin_path": str(home / "plugins/observ-hermes"),
               "session_ids": [], "cancel_requested": False, "cancelled": False}
    expected_version = ctx.runtime_version or "0.18.2"
    if code or version != expected_version:
        return AgentResult(code or 1, None, version, version_output, {**details, "error":
            f"Hermes native acceptance requires hermes-agent {expected_version}"})
    if ctx.scenario == "cancel":
        code, output, session_id, requested, cancelled = _cancel_acp(ctx, env, deadline)
        details.update(session_ids=[session_id], cancel_requested=requested, cancelled=cancelled,
                       cancellation_protocol="official-acp")
        if capture_native:
            details["hermes_raw_callbacks"] = _raw_callbacks(ctx.out / "raw-callbacks")
        return AgentResult(code, session_id, version, output, details)
    outputs = []
    session_id = None
    prompts = [ctx.prompt] + ([ctx.followup_prompt] if ctx.scenario == "multi-turn" else []) + list(additional_prompts)
    for index, prompt in enumerate(prompts):
        usage_file = ctx.out / f"native-usage-{index + 1}.json" if version == "0.21.5" else None
        if usage_file:
            # Official one-shot accounting joins the deferred title thread.
            # Plain chat -q exits before its auxiliary client necessarily ends.
            command = [*ctx.runtime, "-z", prompt, "--usage-file", str(usage_file),
                       "--provider", provider, "--model", ctx.model, "--toolsets", "file"]
        else:
            command = [*ctx.runtime, "chat", "-q", prompt, "--provider", provider, "--model", ctx.model,
                       "--toolsets", "file", "--max-turns", "4", "--ignore-rules", "-Q"]
        if index:
            if not session_id:
                return AgentResult(1, None, version, "\n".join(outputs), {**details, "error": "Hermes returned no session to resume"})
            command.extend(["--resume", session_id])
        code, output, _ = _launch(command, ctx, env, deadline)
        outputs.append(output)
        if usage_file:
            try:
                native_usage = json.loads(usage_file.read_text(encoding="utf-8"))
                returned_session = native_usage.get("session_id")
                if not isinstance(returned_session, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", returned_session):
                    raise ValueError("No native session identity")
                details.setdefault("native_usage_reports", []).append(native_usage)
                if native_usage.get("completed") is not True:
                    code = code or 1
            except (OSError, ValueError, AttributeError):
                returned_session = None
                code = code or 1
                details["error"] = "Hermes one-shot usage report is missing or lacks its native session identity"
        else:
            returned_session = _session(output)
        details["session_ids"].append(returned_session)
        if index and returned_session != session_id:
            code = code or 1
            details["error"] = "Hermes resume changed the native session identity"
        elif index:
            details["resumed_session_id"] = returned_session
        session_id = returned_session
        if code:
            break
    if capture_native:
        details["hermes_raw_callbacks"] = _raw_callbacks(ctx.out / "raw-callbacks")
    return AgentResult(code, session_id, version, "\n".join(outputs), details)
