#!/usr/bin/env python3
"""Native hook launcher: load private configuration and refresh ingest credentials."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import urllib.parse

try:  # Not available on every platform; stdin waiting degrades below.
    import select
except ImportError:  # pragma: no cover
    select = None


class CollectionModeError(ValueError):
    pass


def loopback_host(url):
    """Recognize explicit loopback targets without resolving remote hostnames."""
    host = urllib.parse.urlsplit(url).hostname
    if host == 'localhost':
        return host
    try:
        return host if ipaddress.ip_address(host).is_loopback else None
    except ValueError:
        return None


def write_private(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def token_for(config_path: Path) -> tuple[dict, dict]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cache_path = config_path.with_name("ingest-token.json")
    try:
        token = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        token = {}
    # Never reuse a token left by another Agent or ingest endpoint, including
    # the fallback used when the server is temporarily unavailable.
    if any(token.get(key) != config.get(key) for key in ("space_id", "agent_uuid", "endpoint")):
        token = {}
    cached = token
    now = time.time()
    if token.get("valid_until", 0) > now + 60 and token.get("space_id") == config["space_id"]:
        return config, token
    request = urllib.request.Request(
        config["base_url"].rstrip("/") + "/openapi/v1/ingest-tokens",
        data=json.dumps({"agent_uuid": config["agent_uuid"]}).encode(),
        headers={
            "Authorization": "Bearer " + (config.get("agent_credential") or config["space_key"]),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # No redirects: never forward an installation credential to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        handlers = [NoRedirect]
        if loopback_host(request.full_url):
            handlers.append(urllib.request.ProxyHandler({}))
        with urllib.request.build_opener(*handlers).open(request, timeout=2) as response:
            payload = json.load(response)
        if payload.get("code") != 0:
            raise ValueError("Mint rejected")
        token = payload["data"]
        if token["space_id"] != config["space_id"]:
            raise ValueError("Space changed; reinstall to switch spaces")
        if token["agent_uuid"] != config["agent_uuid"]:
            raise ValueError("Agent changed; reinstall to switch Agents")
        if token["endpoint"].rstrip("/") != config["endpoint"].rstrip("/"):
            raise ValueError("Ingest endpoint changed; reinstall to update configuration")
        token["valid_until"] = now + int(token["expires_in"])
        write_private(cache_path, json.dumps(token))
        return config, token
    except Exception:
        if cached.get("valid_until", 0) > now:
            return config, cached
        raise


def hook_env(config: dict, token: dict, *, environ=None) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    endpoint = config["endpoint"].rstrip("/")
    host = loopback_host(endpoint)
    if host:
        # Exporters also inherit proxy settings. Only add this local target to
        # their bypass list, preserving both spellings and the parent's proxies.
        bypass = [item.strip() for value in (env.get('NO_PROXY', ''), env.get('no_proxy', ''), host)
                  for item in value.split(',') if item.strip()]
        if ':' in host:
            # Rust http::Uri retains IPv6 brackets; other clients use bare IPs.
            bypass.append(f'[{host}]')
        env['NO_PROXY'] = env['no_proxy'] = ','.join(dict.fromkeys(bypass))
    for signal in ("traces", "logs", "metrics"):
        if endpoint.endswith("/v1/" + signal):
            endpoint = endpoint.rsplit("/v1/", 1)[0]
            break
    header = "Authorization=Bearer " + token["token"]
    agent = config["agent"]
    prefix = {"codex": "CDXHOOKS", "claude-code": "CCHOOKS", "kimi": "KIMI"}[agent]
    mode = env.get('AIVISION_COLLECTION_MODE', env.get(prefix + '_COLLECTION_MODE', config.get('collection_mode', 'full')))
    if not isinstance(mode, str) or mode.strip().lower() not in ('full', 'privacy'):
        raise CollectionModeError('Invalid collection mode; telemetry disabled')
    env['AIVISION_COLLECTION_MODE'] = mode.strip().lower()
    env[prefix + '_COLLECTION_MODE'] = mode.strip().lower()
    managed = Path(config["config_dir"]) / "aivision" / agent
    env["AIVISION_MANAGED_INGEST"] = "1"
    env["AIVISION_INGEST_CONFIG"] = str(managed / "ingest.json")
    for kind in ("STATE", "LOG", "SPOOL"):
        env[f"{prefix}_{kind}_DIR"] = str(managed / kind.lower())
    for key in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        prefix + "_LANGFUSE_PUBLIC_KEY",
        prefix + "_LANGFUSE_SECRET_KEY",
        prefix + "_SESSION_DIR",
        "AIVISION_SPACE_KEY",
        "AIVISION_ANT_USERNAME",
        "LANGFUSE_ATTR_USERNAME",
        "LANGFUSE_ATTR_ANT_USERNAME",
        prefix + "_USERNAME",
    ):
        env.pop(key, None)
    env["CCHOOKS_TRANSCRIPT_CAPTURE"] = "false"
    env[prefix + "_LANGFUSE_BASE_URL"] = endpoint + "/v1/traces"
    env[prefix + "_ENABLE_TELEMETRY"] = "1"
    env[prefix + "_DEBUG"] = "0"
    env[prefix + "_METRICS_ENDPOINT"] = endpoint + "/v1/metrics"
    env[prefix + "_LANGFUSE_HEADERS"] = header
    env[prefix + "_AGENT_PRODUCT"] = config["product"]
    if agent == "kimi":
        env["AIVISION_AGENT_PRODUCT"] = config["product"]
        env["KIMI_CODE_HOME"] = config["config_dir"]
        env["KIMI_SHARE_DIR"] = config["config_dir"]
        env["KIMI_OTLP_HEADERS"] = header
        env["KIMI_SESSIONS_ROOT"] = str(Path(config["config_dir"]) / "sessions")
        env["KIMI_CONFIG_DIR"] = str(managed / "config")
        for signal in ("TRACES", "LOGS", "METRICS", "STATUS"):
            env[f"KIMI_OTLP_{signal}_ENDPOINT"] = endpoint + "/v1/" + ("metrics" if signal == "STATUS" else signal.lower())
            env[f"KIMI_OTLP_{signal}_HEADERS"] = header
    else:
        env[prefix + "_OTEL_EXPORTER_OTLP_HEADERS"] = header
        for signal in ("TRACES", "LOGS", "METRICS"):
            env[f"{prefix}_OTEL_EXPORTER_OTLP_{signal}_ENDPOINT"] = endpoint + "/v1/" + signal.lower()
    if agent == "codex":
        env["CODEX_HOME"] = config["config_dir"]
    elif agent == "claude-code":
        env["CLAUDE_CONFIG_DIR"] = config["config_dir"]
    return env


def configure_content_logging(config, env):
    """Translate the pinned managed mode for Kimi's legacy content switches."""
    if config['agent'] != 'kimi':
        return
    for signal in ('SPANS', 'EVENTS'):
        for content in ('USER_PROMPTS', 'MODEL_OUTPUTS', 'TOOL_DETAILS', 'TOOL_OUTPUTS'):
            key = f'KIMI_LOG_{signal}_{content}'
            if env['AIVISION_COLLECTION_MODE'] == 'privacy':
                env[key] = 'false'
            else:
                # Full mode opts into content. Keep deliberate per-field env
                # opt-outs; the legacy builtin defaults otherwise redact it all.
                env.setdefault(key, 'true')


def pin_session_mode(config, env, session_id):
    """Keep hook subprocesses and their workers on the session's initial mode."""
    if not session_id:
        return
    directory = Path(config['config_dir']) / 'aivision' / config['agent'] / 'session-modes'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    path = directory / (digest + '.json')
    descriptor = os.open(directory / (digest + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'r+b') as lock:
        if os.name == 'nt':
            import msvcrt

            # Lock one byte from offset zero, including on a new empty file.
            # Windows releases the byte-range lock when the file is closed.
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            mode = json.loads(path.read_text())['collection_mode']
            if mode not in ('full', 'privacy'):
                raise CollectionModeError('Invalid saved session mode')
        else:
            mode = env['AIVISION_COLLECTION_MODE']
            write_private(path, json.dumps({'collection_mode': mode}))
        env['AIVISION_COLLECTION_MODE'] = mode
        prefix = {'codex': 'CDXHOOKS', 'claude-code': 'CCHOOKS', 'kimi': 'KIMI'}[config['agent']]
        env[prefix + '_COLLECTION_MODE'] = mode


# Kept in sync with agentic-otel-common `STDIN_TIMEOUT_MS`: the native hook
# abandons its stdin read after this budget, so the launcher must not block longer.
STDIN_READ_TIMEOUT_MS = 1000
STDIN_READ_CHUNK_BYTES = 65536
# Kept in sync with cdxhooks `common::dirs`: `{sanitized[:80]}-{sha256[:16]}`.
SESSION_KEY_SAFE_PREFIX_CHARS = 80
SESSION_KEY_HASH_HEX_CHARS = 16
SESSION_KEY_HASH_DOMAIN = b"cdxhooks-session-key:"


def _select_can_wait_on(fd: int) -> bool:
    """True when select() accepts this descriptor (POSIX pipes and files)."""
    if select is None:
        return False
    try:
        select.select([fd], [], [], 0)
        return True
    except (OSError, ValueError):
        return False


def _drain_fd(fd: int, collected: dict) -> None:
    chunks: list = []
    try:
        while True:
            chunk = os.read(fd, STDIN_READ_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
    except (OSError, ValueError):
        pass
    collected["payload"] = b"".join(chunks)


def read_stdin_with_timeout(timeout_ms: int = STDIN_READ_TIMEOUT_MS) -> bytes:
    """Read the hook payload by a deadline, leaving no blocked reader behind.

    agentic-otel-common gives up on stdin after `STDIN_TIMEOUT_MS`, and the
    launcher must not block longer. A helper thread left inside
    `sys.stdin.buffer.read()` keeps the buffered reader's lock held, and
    interpreter shutdown then aborts with `_enter_buffered_busy` (SIGABRT), so
    drain the raw descriptor with a select deadline instead.
    """
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return b""

    deadline = time.monotonic() + timeout_ms / 1000.0
    if _select_can_wait_on(fd):
        chunks: list = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                break
            if not ready:
                break
            try:
                chunk = os.read(fd, STDIN_READ_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    # Platforms whose select() cannot wait on stdin keep the deadline with a
    # raw-descriptor reader thread, which holds no buffered lock.
    collected: dict = {}
    reader = threading.Thread(target=_drain_fd, args=(fd, collected), daemon=True)
    reader.start()
    reader.join(timeout_ms / 1000.0)
    return collected.get("payload", b"")


def extract_session_id(payload) -> str:
    """Mirror agentic-otel-common `extract_session_id` key precedence."""
    if not isinstance(payload, dict):
        return ""
    for key in ("sessionId", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    session = payload.get("session")
    if isinstance(session, dict):
        value = session.get("id")
        if isinstance(value, str) and value:
            return value
    return ""


def session_state_key(session_id: str):
    """Mirror cdxhooks `session_key` so recovery can locate a session's state."""
    raw = (session_id or "").strip()
    if not raw:
        return None
    sanitized = "".join(
        char if (char.isascii() and char.isalnum()) or char in "._-" else "_"
        for char in raw
    )
    readable = (
        "session"
        if not sanitized or set(sanitized) == {"."}
        else sanitized[:SESSION_KEY_SAFE_PREFIX_CHARS]
    )
    suffix = hashlib.sha256(SESSION_KEY_HASH_DOMAIN + raw.encode()).hexdigest()
    return f"{readable}-{suffix[:SESSION_KEY_HASH_HEX_CHARS]}"


def recover_latest_detached_worker(
    binary: str, env: dict, current_session_id=None
):
    """Recover a macOS worker whose parent hook exited during handoff.

    Recovery is scoped to the hook's own session: without a valid session id we
    cannot tell which marker this hook owns, and guessing (newest marker) could
    resume an unrelated session, so we skip instead.
    """
    if os.name == "nt":
        return
    session_key = session_state_key(current_session_id or "")
    if not session_key:
        return
    state_root = Path(env.get("CDXHOOKS_STATE_DIR", ""))
    if not state_root.is_dir():
        return

    # The state directory is derived from the session id, so recovery does not
    # depend on `state_identity.json`: the worker writes that file only after it
    # starts processing, and a worker that dies before writing it still has to
    # be recovered.
    session_dir = state_root / session_key
    marker_path = session_dir / "worker.starting.json"

    lock_file = None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("status") != "starting" or not marker.get("token"):
            return
        # Belt and braces: when the identity file exists it must agree.
        try:
            identity = json.loads(
                (session_dir / "state_identity.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            identity = None
        if isinstance(identity, dict) and identity.get("session_id") not in (
            None,
            current_session_id,
        ):
            return

        import fcntl

        lock_file = (session_dir / "worker.lock").open("a+")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        worker_env = env.copy()
        worker_env.update(
            {
                "CDXHOOKS_INTERNAL_WORKER": "true",
                "CDXHOOKS_WORKER_SESSION_ID": current_session_id,
                "CDXHOOKS_WORKER_START_TOKEN": marker["token"],
            }
        )
        subprocess.Popen(
            [binary, "__worker"],
            env=worker_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        lock_file = None
    except (OSError, ValueError, TypeError, KeyError):
        # Telemetry must remain fail-open for the Agent hook.
        return
    finally:
        if lock_file is not None:
            try:
                lock_file.close()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    try:
        config, token = token_for(args.config)
        # The host payload and any hook control response pass through untouched.
        environment = hook_env(config, token)
        payload = read_stdin_with_timeout()
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            parsed = None
        pin_session_mode(config, environment, extract_session_id(parsed))
        configure_content_logging(config, environment)
        subprocess.run([args.binary], input=payload, env=environment, check=False)
        recover_latest_detached_worker(
            args.binary, environment, extract_session_id(parsed)
        )
    except CollectionModeError:
        print("AI Vision: invalid collection mode; telemetry disabled. Expected full or privacy.", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f"AI Vision: ingest credential request returned HTTP {exc.code}; check the deployment and Agent credentials. Agent task continues.", file=sys.stderr)
    except urllib.error.URLError:
        # Exception text can contain credential-bearing proxy URLs. Keep only
        # actionable context, never the underlying URL, headers or response.
        print("AI Vision: cannot refresh ingest credentials; check AI Vision connectivity and proxy/NO_PROXY settings. Agent task continues.", file=sys.stderr)
    except Exception:
        # Collection failure must not reject the user's Agent task or leak secrets.
        print("AI Vision: telemetry unavailable; Agent task continues.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
