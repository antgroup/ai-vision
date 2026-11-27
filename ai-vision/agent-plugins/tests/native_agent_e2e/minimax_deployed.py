"""Qualify the installed MiniMax Code plugin against an isolated deployed AI Vision."""
from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from langchain_core.messages import AIMessage

from fake_provider import FakeProvider, _content, _receipt, _tool_results


PRODUCT = "MiniMaxCode"
VERSION = "0.7.2"
TOOL_READY = "cancel-tool.ready"
CONTENT = ("gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result")
USAGE = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.cache_read.input_tokens",
         "gen_ai.usage.cache_creation.input_tokens")
TRACE_ID = r"[0-9a-f]{32}"
SHA256 = r"[0-9a-f]{64}"


class QualificationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise QualificationError(message)


class ToolFixture(FakeProvider):
    """Make the real native host read a fixture and run its own shell tool."""

    def reply(self, body, number, **options):
        results = _tool_results(body.get("messages", []))
        if self.scenario == "cancel" or len(results) != 1 or not _receipt(results):
            if self.scenario == "cancel-tool" and len(results) > 1:
                raise ValueError("The shell request completed before native cancellation")
            if len(results) == 2 and self.scenario != "cancel" and not re.fullmatch(r"\s*1\s+receipt\.txt\s*", _content(results[1])):
                raise ValueError("The native shell did not return the expected file line count")
            return super().reply(body, number, **options)
        tool = next((item.get("function", item) for item in body.get("tools", [])
                     if item.get("function", item).get("name") == "bash"), None)
        if not tool:
            raise ValueError("The native CLI did not advertise its shell tool")
        command = "wc -l receipt.txt"
        if self.scenario == "cancel-tool":
            command = f"printf ready > {TOOL_READY}; sleep 45"
        return AIMessage(content="", tool_calls=[{
            "id": f"call_native_{number}", "name": tool["name"], "args": {"command": command}, "type": "tool_call",
        }])

    def usage(self, row):
        usage = super().usage(row)
        if usage is None or row["purpose"] != "agent":
            return usage
        number = row["number"]
        return {"prompt_tokens": 100 + 7 * number, "completion_tokens": 8 + number,
                "total_tokens": 108 + 8 * number, "prompt_tokens_details": {"cached_tokens": 3}}


def credential_file(path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) & 0o077 == 0,
            "Qualification account or model file must be a private regular file")
    return path.read_text(encoding="utf-8")


def model_config():
    key_file = os.environ.get("AIVISION_MINIMAX_LIVE_KEY_FILE")
    if not key_file:
        return None
    require(not os.environ.get("GITHUB_ACTIONS"), "Hosted qualification uses the local model fixture")
    lines = credential_file(Path(key_file)).strip().splitlines()
    key = next((line.partition("=")[2].strip().strip("\"'") for line in lines
                if line.startswith("OPENAI_API_KEY=")), lines[0] if len(lines) == 1 else "")
    require(bool(key) and "\n" not in key, "The explicit local OpenAI key is missing")
    return "gpt-5.4-mini", key


def clean_origin(value):
    origin = urlsplit(value)
    require(origin.scheme in {"http", "https"} and origin.hostname and not origin.username and not origin.password
            and origin.path in ("", "/") and not origin.query and not origin.fragment,
            "Supply the deployed AI Vision origin with no credentials or path")
    try:
        local = ipaddress.ip_address(origin.hostname).is_loopback
    except ValueError:
        local = origin.hostname == "localhost"
    require(origin.scheme == "https" or local, "HTTP qualification is restricted to a loopback origin")
    return value.rstrip("/")


def api(client, path, body=None, *, params=None):
    if body is None:
        response = client.get(path, params=params)
    else:
        response = client.post(path, json=body, headers={"X-CSRFToken": client.cookies.get("csrftoken", "")})
    try:
        value = response.json()
    except ValueError:
        value = {}
    return response.status_code, value


def expect_api(client, path, label, body=None):
    status, payload = api(client, path, body)
    require(200 <= status < 300 and isinstance(payload, dict) and payload.get("code") == 0,
            f"{label} failed (HTTP {status})")
    return payload.get("data")


def login(client, account):
    expect_api(client, "/api/v1/auth/csrf", "Get account CSRF cookie")
    expect_api(client, "/api/v1/auth/login", "Sign in to the deployed account",
               {"email": account["email"], "password": account["password"]})
    expect_api(client, "/api/v1/auth/csrf", "Refresh account CSRF cookie")
    expect_api(client, "/api/v1/auth/me", "Verify customer session")
    return str(UUID(account["space_id"]))


def package_pin(client, expected):
    catalog = expect_api(client, "/api/public/onboarding/catalog.json", "Read packaged onboarding catalog")
    entry, = [entry for entry in catalog["agents"] if entry["key"] == "minimax-code"]
    require(entry["plugin"].get("preview") is True and entry["plugin"].get("product") == PRODUCT,
            "MiniMax Code preview entry was missing or had a different product")
    archive, = [item for item in entry["downloads"] if (item["platform"], item["arch"]) == ("all", "all")]
    digest = archive["sha256"]
    require(re.fullmatch(SHA256, digest) and (not expected or digest == expected), "Deployed preview artifact checksum mismatch")
    return digest


def invoke(command, label, env, work, timeout=60):
    run = subprocess.run(command, env=env, cwd=work, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    require(run.returncode == 0, f"{label} failed (exit {run.returncode})")
    return run.stdout


def install(client, args, space, digest, env, task):
    path = f"/api/v1/spaces/{space}/agents"
    record = expect_api(client, path + "/create", "Create MiniMax Code from Agent Market", {"agent_product": PRODUCT})
    agent = str(UUID(record["agent_uuid"]))
    require(record.get("agent_product") == PRODUCT, "Market registration returned the wrong Agent product")
    grant = expect_api(client, f"{path}/{agent}/installation-tokens", "Issue one-use installation grant", {})
    ticket = grant["installation_token"]
    require(grant.get("agent_uuid") == agent and grant.get("space_id") == space and ticket.startswith("aiv-install-"),
            "Market installation grant returned the wrong scope")
    invoke([str(args.aiv), "connect", "minimax-code", "--base-url", args.base_url, "--space-id", space,
            "--agent-uuid", agent, "--installation-token", ticket, "--config-dir", env["MINIMAX_DATA_DIR"]],
           "Packaged aiv connect with Market grant", env, task)
    managed = Path(env["MINIMAX_DATA_DIR"]) / "aivision/minimax-code"
    receipt = json.loads((managed / "installation.json").read_text(encoding="utf-8"))
    require(receipt.get("agent") == "minimax-code" and receipt.get("sha256") == digest and
            receipt.get("host_version") == VERSION and (Path(receipt["installed_path"]) / "dist/index.js").is_file(),
            "The installed launcher receipt does not match the deployed package")
    token = json.loads((managed / "ingest-token.json").read_text(encoding="utf-8"))["token"].split(".")[0]
    verified = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    require(verified.get("sp") == space and verified.get("aid") == agent and verified.get("ap") == PRODUCT
            and isinstance(verified.get("u"), str) and verified["u"], "Installed ingest token has no authoritative scope")
    require_connected(client, space, agent)
    return agent, verified["u"], managed / "run"


def require_connected(client, space, agent):
    records = expect_api(client, f"/api/v1/spaces/{space}/agents", "Read Agent Market connection status")
    require(any(item.get("agent_uuid") == agent and item.get("status") == 1 for item in records),
            "Agent Market did not confirm the installed Agent")


def wait_detail(client, space, agent, trace, timeout):
    path = f"/api/v1/spaces/{space}/signoz/trace/{trace}/detail"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = api(client, path, params={"agent_product": PRODUCT, "agent_uuid": agent})
        detail = payload.get("data") or {} if isinstance(payload, dict) else {}
        require(status not in (401, 403), f"Trace readback was denied (HTTP {status})")
        if status == 200 and detail.get("meta", {}).get("complete") is True and detail.get("spans"):
            return detail
        time.sleep(1)
    raise QualificationError(f"Timed out waiting for the complete Trace {trace}")


def run_turn(launcher, env, task, model, prompt, mode, *, session=None, cancel=None):
    command = [str(launcher), "--cwd", str(task), "--permission", "full", "--max-steps", "8", "--model", model]
    if session:
        command += ["--session", session]
    sent = waiting = False
    events = []
    with tempfile.TemporaryFile() as stderr:
        with subprocess.Popen([*command, prompt], cwd=task, env={**env, "AIVISION_COLLECTION_MODE": mode},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
                              start_new_session=True) as process:
            try:
                pending = b""
                deadline = time.monotonic() + 125
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while time.monotonic() < deadline:
                        if waiting and not sent and (cancel != "tool" or (task / TOOL_READY).is_file()):
                            process.send_signal(signal.SIGINT)
                            sent = True
                        if not selector.select(.1):
                            continue
                        block = os.read(process.stdout.fileno(), 65536)
                        if not block:
                            break
                        *lines, pending = (pending + block).split(b"\n")
                        for line in lines:
                            event = json.loads(line)
                            events.append(event)
                            item = event.get("item") or {}
                            tool = item.get("toolCall") or {}
                            waiting |= (cancel == "tool" and tool.get("name") == "bash" and tool.get("status") == 1
                                        or cancel == "model" and item.get("type") == "agent_message"
                                        and bool(item.get("contentDelta")))
                    else:
                        raise QualificationError("Timed out waiting for the native MiniMax Code task")
                code = process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=70)
                    except subprocess.TimeoutExpired:
                        process.kill()
        stderr.seek(0)
        status = "cancelled" if cancel else "succeeded"
        match = re.search(rb"AI Vision Trace (" + TRACE_ID.encode() + rb") \(([^)]+)\)", stderr.read())
    start, = [event for event in events if event["type"] == "turn.started"]
    completed, = [event for event in events if event["type"] == "exec.completed"]
    require(code == (130 if cancel else 0) and (not cancel or sent) and (not session or start["sessionId"] == session)
            and match is not None and match[2].decode() == status and completed["result"]["status"] == status,
            f"Native task terminal outcome did not match {status} (exit {code}, Trace emitted: {match is not None})")
    return {"trace_id": match[1].decode(), "session_id": start["sessionId"], "turn_id": start["turnId"],
            "mode": mode, "exit_code": code, "status": status, "scenario": cancel or "tool-turn",
            "aggregate": completed["result"].get("usage") or {}, "events": events}


def history_usage(profile, run):
    suffix = base64.urlsafe_b64encode(run["session_id"].encode()).decode().rstrip("=")
    histories = list((profile / "v2/sessions").glob(f"*/*/*/*-session_{suffix}/messages.jsonl"))
    if not histories and run["scenario"] == "model":
        return []
    history, = histories
    messages = []
    for line in history.read_bytes().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # An interrupted host can leave a partial final record.
        if row.get("turn_id") == run["turn_id"]:
            messages.append(row["message"])
    usage = []
    for message in messages:
        if message["role"] != "assistant":
            continue
        raw = message.get("usage") or {}
        if message.get("stopReason") in {"aborted", "cancelled", "error"} and not any(
                raw.get(key) for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")):
            continue
        fresh, output = raw["input"], raw["output"]
        cached, written = raw.get("cacheRead", 0), raw.get("cacheWrite", 0)
        usage.append((fresh + cached + written, output, cached, written))
    return usage


def summarize(detail, run, space, agent, subject):
    require(detail["traceId"] == run["trace_id"] and detail["sessionId"] == run["session_id"],
            "Stored Trace does not match the native session")
    spans = detail["spans"]
    roots, models, tools = [], [], []
    for span in spans:
        attr, resource = span["attributes"], span["resource_attributes"]
        require(span["space_id"] == space and span["agent_product"] == PRODUCT and
                resource["aivision.space.id"] == space and resource["aivision.agent.id"] == agent and
                resource["aivision.agent.product"] == PRODUCT and
                resource["aivision.user.id"] == span["user_id"] == attr["ant.username"] == subject and
                resource["aivision.ingest.key_id"].startswith("agent-installation:") and "user.id" not in attr and
                attr["aivision.otlp.scope.name"] == "observ-minimax-code" and
                attr["agentic.collection.mode"] == run["mode"], "Gateway-assigned Trace identity or collection scope is wrong")
        visible = [json.dumps(attr[key]) for key in CONTENT if key in attr]
        require(all(("[REDACTED]" in value) == (run["mode"] == "privacy") for value in visible),
                "Trace payload did not obey the installed collection mode")
        kind = attr["gen_ai.span.kind"]
        require(kind in ("CHAT", "LLM", "TOOL"), "Trace contains an unexpected span kind")
        {"CHAT": roots, "LLM": models, "TOOL": tools}[kind].append(span)
    root, = roots
    root_attr = root["attributes"]
    require(not root["parent_span_id"] and root_attr["agentic.turn.id"] == run["turn_id"]
            and not any(name in root_attr for name in USAGE) and CONTENT[0] in root_attr,
            "Root span lost its turn identity or duplicated model Token usage")
    def start(span):
        return int(span["payload"]["start_time_unix_nano"])

    def end(span):
        return int(span["payload"]["end_time_unix_nano"])

    require(all(span["parent_span_id"] == root["span_id"] and start(root) <= start(span) <= end(span) <= end(root)
                for span in models + tools),
            "Trace children are not timed siblings under the native turn")
    cancelled = run["status"] == "cancelled"
    require(root["status"]["code"] == ("ERROR" if cancelled else "OK") and
            root_attr.get("error.type") == ("cancelled" if cancelled else None), "Stored turn has the wrong terminal status")
    completions = []
    for span in sorted(models, key=start):
        attr = span["attributes"]
        if attr.get("error.type") == "cancelled":
            require(run["scenario"] == "model" and span["status"]["code"] == "ERROR" and
                    not any(key.startswith("gen_ai.usage.") for key in attr) and
                    all(attr.get(f"agentic.usage.{field}.missing") == "true" for field in ("input_tokens", "output_tokens")),
                    "An interrupted model request reported invented Token usage")
        else:
            require(span["status"]["code"] == "OK", "Committed model request was not successful")
            completions.append(tuple(int(attr[name]) for name in USAGE))
    tool_status = sorted((item["attributes"]["gen_ai.tool.name"], item["status"]["code"],
                          item["attributes"].get("error.type")) for item in tools)
    expected_tools = [] if run["scenario"] == "model" else [("bash", "ERROR", "cancelled") if cancelled else
                                                           ("bash", "OK", None), ("read", "OK", None)]
    require(tool_status == expected_tools and len(models) == {"model": 1, "tool": 2, "tool-turn": 3}[run["scenario"]]
            and (run["scenario"] != "model" or not completions), "Trace did not retain every expected native model and tool span")
    return {"trace_id": run["trace_id"], "session_id": run["session_id"], "turn_id": run["turn_id"],
            "span_ids": sorted(item["span_id"] for item in spans), "spans": len(spans), "mode": run["mode"],
            "turn_number": int(root_attr["agentic.turn.number"]), "status": run["status"], "scenario": run["scenario"],
            "exit_code": run["exit_code"], "per_request_tokens": [list(entry) for entry in completions],
            "model_usage_missing": run["scenario"] == "model", "tools": [list(tool) for tool in tool_status]}


def qualify(client, args, space, digest, root):
    task = root / "task"
    task.mkdir()
    profile = root / "profile"
    env = {"PATH": os.pathsep.join((str(args.mcode.parent), os.environ["PATH"])), "HOME": str(root),
           "MINIMAX_DATA_DIR": str(profile), "AIVISION_CONFIG_DIR": str(root / "aiv"), "AIVISION_PYTHON": sys.executable,
           "MCODE_DISABLE_TELEMETRY": "1", "PYTHONUTF8": "1", "MINIMAX_FIXTURE_API_KEY": "local-fixture-key"}
    require(invoke([str(args.mcode), "--version"], "Read pinned MiniMax Code version", env, task).strip() == VERSION,
            "MiniMax Code qualification requires version 0.7.2")
    receipt = task / "receipt.txt"
    live = model_config()
    with (nullcontext() if live else ToolFixture(receipt, scenario="cache-usage")) as provider:
        model = live[0] if live else "native-e2e-chat"
        native_env = env.copy()
        if live:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                if name in os.environ:
                    native_env[name] = os.environ[name]
            bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            bypass = ",".join(filter(None, (bypass, "localhost", "127.0.0.1", "::1", urlsplit(args.base_url).hostname)))
            native_env.update(OPENAI_API_KEY=live[1], NODE_OPTIONS="--use-system-ca", NODE_USE_ENV_PROXY="1",
                              NO_PROXY=bypass, no_proxy=bypass)
        invoke([str(args.mcode), "provider", "add", "--name", "AI Vision Fixture", "--model", model,
                "--base-url", "https://api.openai.com/v1" if live else provider.url,
                "--api-format", "openai-responses" if live else "openai-completions",
                "--api-key-env", "OPENAI_API_KEY" if live else "MINIMAX_FIXTURE_API_KEY"],
               "Configure the qualified MiniMax Code model", native_env, task)
        agent, subject, launcher = install(client, args, space, digest, env, task)
        session, reports = None, []
        for label, mode, cancel in (("initial-tool-turn", "full", None), ("resumed-tool-turn", "privacy", None),
                                    ("active-tool-cancel", "privacy", "tool"), ("model-stream-cancel", "privacy", "model")):
            marker = uuid4().hex
            receipt.write_text(f"receipt: {marker}\n", encoding="utf-8")
            prompt = ("Read receipt.txt with the read tool, then run exactly wc -l receipt.txt using the bash tool. "
                      "Use both tools in order, then return the receipt verbatim followed by the line count.")
            if cancel == "tool":
                prompt = f"Read receipt.txt, then run exactly `printf ready > {TOOL_READY}; sleep 45` using bash and wait for it."
            elif cancel == "model":
                prompt = "Start writing a long numbered list of fictional garden plants without using any tools."
            if provider:
                provider.scenario = {"tool": "cancel-tool", "model": "cancel"}.get(cancel, "cache-usage")
                before = len(provider.requests)
            run = run_turn(launcher, native_env, task, "custom_provider:ai-vision-fixture/" + model, prompt, mode,
                           session=None if cancel == "model" else session, cancel=cancel)
            if not session:
                session = run["session_id"]
            detail = wait_detail(client, space, agent, run["trace_id"], args.trace_timeout)
            report = summarize(detail, run, space, agent, subject)
            actual = report["per_request_tokens"]
            require(actual == [list(usage) for usage in history_usage(profile, run)],
                    "AI Vision did not retain exact per-request usage from MiniMax committed history")
            if provider:
                calls = [call for call in provider.requests[before:] if call["purpose"] == "agent"]
                expected = [[call["usage"]["prompt_tokens"], call["usage"]["completion_tokens"], 3, 0]
                            for call in calls if call["usage_reported"]]
                require(actual == expected and len(calls) == (1 if cancel == "model" else len(expected)),
                        "AI Vision per-request usage did not match the independent model transport")
            if not cancel:
                totals = [sum(row[0] - row[2] - row[3] for row in actual),
                          *[sum(row[index] for row in actual) for index in (1, 2, 3)]]
                require(totals == [run["aggregate"].get(key, 0) for key in
                                   ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")],
                        "Stored per-request usage did not match the completed CLI turn totals")
                require(marker in json.dumps(run["events"]) and (marker in json.dumps(detail)) == (mode == "full"),
                        "Native tool receipt or Privacy readback was incorrect")
            require(report["turn_number"] == (1 if cancel == "model" else len(reports) + 1) and
                    (cancel != "model" or session != report["session_id"]), "Native session continuity was incorrect")
            report["label"] = label
            reports.append(report)
    require(len({report["trace_id"] for report in reports}) == len(reports), "Native tasks did not have distinct Trace IDs")
    return {"space_id": space, "agent_uuid": agent, "subject": subject, "artifact_sha256": digest,
            "model_mode": "openai-byok" if live else "loopback-fixture", "traces": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", type=clean_origin, required=True)
    parser.add_argument("--aiv", type=Path, required=True)
    parser.add_argument("--mcode", type=Path, required=True)
    parser.add_argument("--phase", choices=("initial", "recast"), required=True)
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--trace-timeout", type=int, default=60)
    args = parser.parse_args()
    os.umask(0o077)
    require(os.name == "posix", "Deployed qualification uses native POSIX cancellation")
    require(0 < args.trace_timeout <= 180 and not args.state_dir.is_symlink(), "Invalid qualification state directory or timeout")
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(stat.S_IMODE(args.state_dir.stat().st_mode) & 0o077 == 0, "Qualification state directory is not private")
    args.aiv, args.mcode = args.aiv.absolute(), args.mcode.absolute()
    status_path = args.state_dir / "readback.json"
    account = json.loads(credential_file(args.account_file))
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=15,
                      headers={"Origin": args.base_url, "Referer": args.base_url + "/", "Accept": "application/json"}) as client:
        space = login(client, account)
        digest = package_pin(client, args.artifact_sha256)
        if args.phase == "initial":
            require(not status_path.exists(), "Qualification state already contains an initial run")
            with tempfile.TemporaryDirectory(prefix="aiv-minimax-deployed-", dir=args.state_dir) as path:
                state = qualify(client, args, space, digest, Path(path).resolve())
            with status_path.open("x", encoding="utf-8") as file:
                json.dump(state, file, sort_keys=True)
        else:
            state = json.loads(credential_file(status_path))
            require(state["space_id"] == space and state["artifact_sha256"] == digest and len(state["traces"]) == 4,
                    "Recast deployment changed the test space, packaged plugin, or expected Trace set")
            require_connected(client, space, state["agent_uuid"])
            for saved in state["traces"]:
                detail = wait_detail(client, space, state["agent_uuid"], saved["trace_id"], args.trace_timeout)
                reread = summarize(detail, saved, space, state["agent_uuid"], state["subject"])
                require(all(saved.get(key) == value for key, value in reread.items()), "A Trace changed after recast")
    return {"status": "passed", "phase": args.phase, "artifact_sha256": digest, "model_mode": state["model_mode"],
            "trace_count": len(state["traces"]), "traces": [{key: value for key, value in report.items()
            if key not in ("span_ids", "session_id", "turn_id")} for report in state["traces"]]}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True), flush=True)
    except QualificationError as error:
        print(f"MiniMax qualification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, subprocess.SubprocessError) as error:
        print(f"MiniMax qualification failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
