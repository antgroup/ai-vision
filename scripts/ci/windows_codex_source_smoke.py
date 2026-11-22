"""Verify the opt-in Windows CLI's Codex installer against a loopback service."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request


REPO = Path(__file__).resolve().parents[2]
PLUGINS = REPO / "ai-vision" / "agent-plugins"
CODEX_VERSION = "0.161.0"
CODEX_ARCHIVE = (
    f"https://registry.npmjs.org/@openai/codex/-/codex-{CODEX_VERSION}-win32-x64.tgz"
)
CODEX_ARCHIVE_SHA512 = (
    "VdNnttGOG3nbwoREvVurVBXci/EJD/kncVJnvtXjpEE+YhC1twvvrVNmtlZcZGEEVi06xLN/ov0d6nxjZ1XLSg=="
)
CODEX_MEMBER = "package/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
MUTATIONS = ("exchanges", "refreshes", "confirmations", "reports")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def official_codex(destination):
    # Reuse the existing native harness's Codex version. Read only the one
    # expected regular file after authenticating the official platform archive.
    archive = destination.with_suffix(".tgz")
    digest = hashlib.sha512()
    size = 0
    with urllib.request.urlopen(CODEX_ARCHIVE, timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            require(size <= 256 * 1024 * 1024, "Codex Windows archive exceeds the test download limit")
            digest.update(chunk)
            output.write(chunk)
    actual = base64.b64encode(digest.digest()).decode("ascii")
    require(actual == CODEX_ARCHIVE_SHA512, "Codex Windows package failed SHA-512 verification")
    with tarfile.open(archive, mode="r:gz") as package:
        member = package.getmember(CODEX_MEMBER)
        require(member.isfile() and 0 < member.size <= 512 * 1024 * 1024, "Expected the official Windows Codex executable")
        with package.extractfile(member) as binary, destination.open("wb") as output:
            shutil.copyfileobj(binary, output)
    archive.unlink()
    return destination


def smoke_env(root, host):
    excluded = ("AIVISION_", "CDXHOOKS_", "CODEX_", "LANGFUSE_", "OPENAI_", "OTEL_")
    isolated = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(excluded)
        and key.upper() not in {"TRACEPARENT", "TRACESTATE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    }
    isolated.update(
        AIVISION_CONFIG_DIR=str(root / "CLI profile"),
        NO_PROXY="127.0.0.1,localhost",
        PATH=str(host.parent) + os.pathsep + os.environ["PATH"],
        PYTHONUTF8="1",
    )
    return isolated


def execute(stage, command, env, cwd, *, payload=None, rejection=None):
    result = subprocess.run(
        command, env=env, cwd=cwd, input=payload, capture_output=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if rejection is not None:
        require(result.returncode != 0 and rejection in result.stderr, f"{stage}: expected an explicit rejection")
    elif result.returncode != 0:
        # Commands only see a synthetic fixture. Avoid replaying command lines,
        # response bodies, or arbitrary child output into the hosted log.
        raise RuntimeError(f"{stage}: process exited {result.returncode}")
    return result.stdout


def seed_existing_registration(native):
    native.mkdir()
    previous = {"type": "command", "command": "cmd /d /c exit 0", "timeout": 5}
    (native / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [previous]}]}}), encoding="utf-8")
    (native / "config.toml").write_text('model = "ci-synthetic-model"\n', encoding="utf-8")
    return previous


def installed_command(native, hook, previous):
    managed = native / "aivision" / "codex"
    installation = json.loads((managed / "installation.json").read_text(encoding="utf-8"))
    binary = Path(installation["installed_path"]).resolve()
    require(binary.is_relative_to(managed.resolve()), "Installed hook escaped the selected Codex directory")
    require(hashlib.sha256(binary.read_bytes()).digest() == hashlib.sha256(hook.read_bytes()).digest(),
            "Installed hook differs from the checksum-verified build")
    command = subprocess.list2cmdline([
        sys.executable, str(managed / "hook_runtime.py"), "--config", str(managed / "ingest.json"), "--binary", str(binary),
    ])
    recorded = json.loads((native / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in EVENTS:
        commands = [entry for group in recorded[event] for entry in group["hooks"]]
        require(sum(entry.get("command") == command for entry in commands) == 1,
                f"{event} needs exactly one correctly quoted managed command")
        if event == "Stop":
            require(commands.count(previous) == 1 and len(commands) == 2, "Existing unmanaged Stop hook changed")
        else:
            require(len(commands) == 1, f"Unexpected {event} hook registration")
    configuration = tomllib.loads((native / "config.toml").read_text(encoding="utf-8"))
    features = configuration.get("features", {})
    require(configuration.get("model") == "ci-synthetic-model", "Existing Codex configuration changed")
    require(features.get("hooks") is True or features.get("codex_hooks") is True, "Codex hooks feature was not enabled")
    return command


def hook_payload(root):
    session = "a180cf5d-bdc2-4d18-acd4-c31d104dcc61"
    turn = "f556679f-9f68-4908-997f-3f3d4992e098"
    transcript = root / "Synthetic transcript.jsonl"
    events = [
        ("session_meta", {"id": session, "cwd": str(root), "thread_source": "user"}),
        ("event_msg", {"type": "task_started", "turn_id": turn}),
        ("turn_context", {"turn_id": turn, "cwd": str(root), "model": "ci-synthetic-model"}),
        ("event_msg", {"type": "user_message", "message": "Synthetic Windows onboarding request"}),
        ("event_msg", {"type": "agent_message", "phase": "final_answer", "message": "Synthetic Windows result"}),
        ("event_msg", {"type": "task_complete", "turn_id": turn, "last_agent_message": "Synthetic Windows result"}),
    ]
    transcript.write_text("".join(
        json.dumps({"timestamp": f"2026-01-01T00:00:0{i}Z", "type": kind, "payload": data}) + "\n"
        for i, (kind, data) in enumerate(events)
    ), encoding="utf-8")
    return json.dumps({
        "hook_event_name": "Stop", "session_id": session, "turn_id": turn, "cwd": str(root),
        "transcript_path": str(transcript), "stop_hook_active": False,
        "last_assistant_message": "Synthetic Windows result",
    })


def smoke(cli, remote_client, hook):
    # The shared capture helper parses only synthetically generated OTLP.
    sys.path.insert(0, str(PLUGINS / "tests" / "native_agent_e2e"))
    from windows_codex_onboarding import SourceOnboardingServer

    # The detached hook worker can briefly keep its binary open on Windows.
    with tempfile.TemporaryDirectory(prefix="AIV Windows source ", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp).resolve()
        host = official_codex(root / "codex.exe")
        native = root / "Isolated Codex profile"
        env = smoke_env(root, host)
        host_version = execute("Codex version", [str(host), "--version"], env, root).strip()
        require(host_version == f"codex-cli {CODEX_VERSION}", "Expected the pinned official Codex version")
        execute("Ordinary Windows CLI boundary", [str(remote_client), "connect", "codex"], env, root,
                rejection="unavailable in the Windows remote client")
        execute("Experimental Windows CLI boundary", [str(cli), "connect", "claude-code"], env, root,
                rejection="supports only Codex")

        with SourceOnboardingServer(PLUGINS, hook) as server:
            identity = ["--base-url", server.endpoint, "--space-id", server.space_id, "--agent-uuid", server.agent_uuid,
                        "--config-dir", str(native)]
            command = [str(cli), "connect", "codex", *identity, "--python", sys.executable,
                       "--installation-token", server.installation_token]
            print("Checking source installer preflight without local writes or credential exchange", flush=True)
            preflight = execute("Codex preflight", [*command, "--dry-run"], env, root)
            require("Preflight checks passed" in preflight and not native.exists(), "Preflight did not remain read-only")
            require(not any(server.counts.get(kind, 0) for kind in MUTATIONS), "Preflight changed fixture state")

            previous = seed_existing_registration(native)
            print("Installing and repeating registration with the same grant", flush=True)
            execute("Codex installation", command, env, root)
            installed_command(native, hook, previous)
            execute("Codex repeat installation", command, env, root)
            registered = installed_command(native, hook, previous)
            require(server.counts["exchanges"] == 1 and server.counts["confirmations"] == 2,
                    "Repeat install did not reuse the saved reporting credential")

            status = json.loads(execute("Source installer status", [sys.executable, str(PLUGINS / "scripts" / "connect"),
                "--status", "--format", "json", "--agent", "codex", *identity], env, root))
            require(status["installed"] and status["registration"] == "verified" and status["credential"] == "valid",
                    "Source status did not verify installation, registration, and credentials")
            require(server.counts["reports"] == 0, "An installer operation emitted unexpected telemetry")

            print("Executing the exact Windows hook command and waiting for authenticated OTLP", flush=True)
            # A raw Windows command line makes CreateProcess parse the saved
            # quoting, including the executable and paths that contain spaces.
            execute("Registered Stop hook", registered, env, root, payload=hook_payload(root))
            deadline = time.monotonic() + 35
            while not server.spans and time.monotonic() < deadline:
                time.sleep(0.1)
            spans = server.spans
            require(any(span.get("name") and re.fullmatch(r"[0-9a-f]{32}", span.get("trace_id", "")) for span in spans),
                    "No valid authenticated OTLP Span arrived after running the Windows hook")
            return {"codex_version": CODEX_VERSION, "installer": "source", "host": "windows-x64",
                    "grant_exchanges": server.counts["exchanges"], "confirmations": server.counts["confirmations"],
                    "managed_hooks": len(EVENTS), "span_count": len(spans), "preflight_read_only": True,
                    "credential_verified": True, "remote_client_boundary_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--remote-client", type=Path, required=True)
    parser.add_argument("--hook", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    require(platform.system() == "Windows" and platform.machine().upper() in {"AMD64", "X86_64"},
            "This smoke test requires native Windows x64")
    revision = os.environ["GITHUB_SHA"]
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Missing tested GitHub merge SHA")
    receipt = smoke(args.cli.resolve(strict=True), args.remote_client.resolve(strict=True), args.hook.resolve(strict=True))
    receipt["source_sha"] = revision
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Native Windows Codex source preflight, install, repeat, status and authenticated hook export passed.")


if __name__ == "__main__":
    main()
