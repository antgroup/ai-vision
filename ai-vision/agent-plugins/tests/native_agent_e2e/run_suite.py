"""Execute real native Agent runtimes against a runner-local fake provider."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from copy import deepcopy
import importlib
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import uuid
from urllib.parse import urlsplit

from capture import Capture
from fake_provider import FakeProvider
from models import CaseContext
from verify import acceptance_status, verify_case
from source_acceptance import assess_source

APPS = ("pi", "hermes", "opencode", "dsh01", "dsh02", "kimi")
SCENARIOS = ("tool-loop", "multi-turn", "tool-error", "stream", "cancel", "unknown-usage", "zero-usage", "cache-usage")
EXTRA_SCENARIOS = {"opencode": ("api-retry",)}
VERSIONS = {"pi": "1.0.2", "hermes": "0.21.5", "opencode": "1.18.34",
            "dsh01": "0.1.7-rc.2", "dsh02": "0.2.0-rc.2", "kimi": "2.1.1"}
PRODUCTS = {"pi": "Pi", "hermes": "Hermes", "opencode": "OpenCode",
            "dsh01": "DeepSeekHarness", "dsh02": "DeepSeekHarness", "kimi": "KimiCLI"}
DRIVERS = {"pi": "pi", "hermes": "hermes", "opencode": "opencode",
           "dsh01": "dsh", "dsh02": "dsh", "kimi": "kimi"}
AGENT_TYPES = {**DRIVERS, "kimi": "kimicli"}
PRIVATE = set()


def safe(value):
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if re.search(r"authorization|cookie|password|secret|space_key|api_key|ingest_token", str(key), re.I) else safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if isinstance(value, str):
        for secret in sorted(PRIVATE, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def selected(value, available):
    rows = value.split(",")
    if not rows or len(rows) != len(set(rows)) or not set(rows).issubset(available):
        raise ValueError("Choose unique entries from " + ",".join(available))
    return rows


def choose_cases(apps, value=None, *, mode="fake"):
    if not apps or len(apps) != len(set(apps)) or not set(apps).issubset(APPS):
        raise ValueError("Choose a nonempty native app inventory")
    if mode=='nvidia' and 'kimi' in apps:
        raise ValueError('Kimi Code native E2E currently supports fake models only')
    if value is None:
        cases = [(app, scenario) for app in apps for scenario in
                 (*SCENARIOS, *(EXTRA_SCENARIOS.get(app, ()) if mode == "fake" else ()))]
    else:
        available = (*SCENARIOS, *(scenario for extra in EXTRA_SCENARIOS.values() for scenario in extra))
        scenarios = selected(value, available)
        cases = [(app, scenario) for app in apps for scenario in scenarios]
        if any(scenario not in (*SCENARIOS, *EXTRA_SCENARIOS.get(app, ())) for app, scenario in cases):
            raise ValueError("The api-retry scenario supports only OpenCode")
    validate_mode(mode, [scenario for _, scenario in cases], os.environ)
    return cases


DEFAULT_CASES = tuple((app, scenario) for app in APPS for scenario in (*SCENARIOS, *EXTRA_SCENARIOS.get(app, ())))


def load_runtimes(path, apps):
    value = json.loads(path.read_text(encoding="utf-8"))
    for app in apps:
        prefix = value.get(app)
        if not isinstance(prefix, list) or not prefix or not all(isinstance(part, str) and part for part in prefix):
            raise ValueError("Supply the official runtime argv prefix for " + app)
    return value


def load_targets(value, apps):
    if not isinstance(value, dict) or not all(app in value for app in apps):
        raise ValueError("Every selected app requires a runner-local platform target")
    for app in apps:
        target = value[app]
        if not isinstance(target, dict) or target.get("data_backend") != "oceanbase":
            raise ValueError("Native platform trace E2E requires OceanBase")
        for field in ("endpoint", "collector_endpoint"):
            parsed = urlsplit(target.get(field, ""))
            host = parsed.hostname or ""
            try:
                local = host == "localhost" or ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
            if parsed.scheme not in ("http", "https") or not local or parsed.username or parsed.password:
                raise ValueError("Platform targets must use credential-free loopback URLs")
        if (not all(target.get(key) for key in ("space_id", "space_key", "agent_uuid", "collector_headers"))
                or target.get("agent_product") != PRODUCTS[app]):
            raise ValueError("Platform target identity or ingestion credentials are incomplete")
        for key in ("space_key", "ingest_token", "dashboard_key"):
            if target.get(key):
                PRIVATE.add(target[key])
        for key in ("collector_headers", "dashboard_headers"):
            PRIVATE.update(target.get(key, {}).values())
    return value


def validate_mode(mode, scenarios, env):
    if mode == "nvidia" and (env.get("GITHUB_ACTIONS") or not set(scenarios).issubset({"tool-loop", "multi-turn", "stream"})):
        raise ValueError("NVIDIA is an explicit local live smoke; CI uses fake models only")


def refresh_ingest(target, post=None):
    import httpx
    if post is None:
        with httpx.Client(timeout=30, trust_env=False) as client:
            return refresh_ingest(target, post=client.post)
    response = post(target["endpoint"].rstrip("/") + "/openapi/v1/ingest-tokens",
        json={"agent_uuid": target["agent_uuid"]}, headers={"Authorization": "Bearer " + target["space_key"]})
    payload = response.json() if response.status_code == 200 else {}
    minted = payload.get("data", {})
    if (payload.get("code") != 0 or minted.get("token_type") != "Bearer" or not minted.get("token")
            or any(str(minted.get(key)) != str(target[key]) for key in ("space_id", "agent_uuid", "agent_product"))):
        raise ValueError("Short-lived ingestion renewal failed or returned the wrong scope")
    PRIVATE.add(minted["token"])
    PRIVATE.add("Bearer " + minted["token"])
    return {**target, "collector_headers": {**target.get("collector_headers", {}),
                                           "Authorization": "Bearer " + minted["token"]}}


class DashboardSessions:
    """Rotate each shared browser session once, never replay an old refresh cookie."""
    def __init__(self, *, client_factory=None, clock=time.monotonic):
        import httpx
        self.client_factory = client_factory or (lambda: httpx.Client(timeout=30, trust_env=False))
        self.clock, self.sessions = clock, {}

    def refresh(self, target):
        headers = target.get("dashboard_headers", {})
        original = headers.get("Cookie", "")
        cookies = SimpleCookie()
        cookies.load(original)
        if "aivision_refresh" not in cookies:
            return target
        endpoint = target["endpoint"].rstrip("/")
        key = (endpoint, cookies["aivision_refresh"].value)
        state = self.sessions.get(key)
        if state is None or self.clock() - state["at"] >= 600:
            current = state["cookies"] if state else {k: v.value for k, v in cookies.items()}
            with self.client_factory() as client:
                csrf = client.get(endpoint + "/api/v1/auth/csrf", headers={
                    "Cookie": "; ".join(k + "=" + v for k, v in current.items())})
                csrf_token = csrf.cookies.get("csrftoken")
                if csrf.status_code != 200 or not csrf_token:
                    raise ValueError("Dashboard CSRF renewal failed")
                current = {**current, "csrftoken": csrf_token}
                response = client.post(endpoint + "/api/v1/auth/refresh", json={},
                    headers={"Origin": endpoint, "Referer": endpoint + "/",
                             "X-CSRFToken": csrf_token,
                             "Cookie": "; ".join(k + "=" + v for k, v in current.items())})
                if (response.status_code != 200 or response.json().get("code") != 0
                        or not response.cookies.get("aivision_access")
                        or not response.cookies.get("aivision_refresh")):
                    raise ValueError("Dashboard session renewal failed")
                # Response cookies replace old entries even when Django uses
                # a narrower refresh-cookie path than the supplied header.
                current.update({k: v for k, v in response.cookies.items()})
            if state is None:
                state = {}
            state.update(cookies=current, at=self.clock())
            self.sessions[key] = state
            self.sessions[(endpoint, current["aivision_refresh"])] = state
            PRIVATE.update(current.values())
        cookie = "; ".join(k + "=" + v for k, v in state["cookies"].items())
        PRIVATE.add(cookie)
        return {**target, "dashboard_headers": {**headers, "Cookie": cookie}}


def execute_case(repo, out, app, scenario, runtime, *, target=None, timeout=180,
                 mode="fake", model="native-e2e-chat", live_provider=None, dashboard_sessions=None):
    receipt = secrets.token_hex(12)
    workspace = out / "workspace"
    workspace.mkdir(parents=True)
    receipt_path = workspace / "receipt.txt"
    receipt_path.write_text("receipt: " + receipt + "\n", encoding="utf-8")
    answer_contract = "Return exactly the complete single line from receipt.txt verbatim, including its literal 'receipt: ' prefix, without formatting or explanation."
    prompt = "Read " + str(receipt_path) + " using your file read tool. " + answer_contract
    resource = {"ant.agent.type": AGENT_TYPES[app], "ant.agent.product": PRODUCTS[app],
                "ant.agent.instance": "native-e2e-" + uuid.uuid4().hex,
                "ant.username": "native-e2e-user"}
    if target:
        target = refresh_ingest(target)
    before = time.time_ns()
    with (live_provider() if live_provider else FakeProvider(receipt_path, scenario,
        length_continuation=mode == 'fake' and app == 'hermes' and scenario == 'multi-turn')) as provider, Capture(
        target.get("collector_endpoint") if target else None,
        target.get("collector_headers") if target else None,
    ) as capture:
        context = CaseContext(repo=repo, out=out / "native", workspace=workspace,
            runtime=tuple(runtime), provider_url=provider.url, collector_url=capture.url,
            app=app, runtime_version=VERSIONS[app],
            scenario=scenario, prompt=prompt, resource=resource, timeout_seconds=timeout, mode=mode, model=model,
            followup_prompt=("Read the same receipt.txt file again with your file read tool. " + answer_contract
                             if scenario == "multi-turn" else ""))
        try:
            result = asdict(importlib.import_module("drivers." + DRIVERS[app]).run_case(context))
        except Exception as error:
            # Failed native runs still retain their original transport/export
            # evidence; absence of a completed session is never fabricated.
            result = {"exit_code": None, "session_id": None, "version": "", "output": "",
                      "details": {"error_type": type(error).__name__, "error": str(error)}}
        # Exporters flush during native shutdown; allow already admitted HTTP
        # handlers to finish, without fabricating extra spans or replay ingress.
        time.sleep(.25)
    # Take immutable snapshots after bounded exporter/provider shutdown,
    # including explicit errors for failed or unfinished attempts.
    with provider.lock:
        requests = deepcopy(provider.requests)
    with capture.lock:
        exports = deepcopy(capture.exports)
    after = time.time_ns()
    try:
        proof = verify_case(app=app, scenario=scenario, model=context.model, receipt=receipt, expected_resource=resource, mode=mode,
                            result=result, requests=requests, exports=exports)
    except Exception as error:
        proof = {"verified": False, "checks": {}, "failures": ["source.oracle_exception"],
                 "error_type": type(error).__name__, "error": str(error)}
    version_ok = bool(result.get("version")) and result["version"].removeprefix("v").split()[0] == VERSIONS[app]
    proof["checks"]["runtime.pinned"] = version_ok
    if not version_ok:
        proof["failures"].append("runtime.pinned")
        proof["verified"] = False
    report = {"case": app + "-" + scenario, "app": app, "scenario": scenario, "mode": mode,
              "version": result["version"], "model": context.model, "bounds": {"from_ms": before // 1000000 - 1, "to_ms": after // 1000000 + 1},
              "receipt": receipt, "expected_resource": resource, "result": result, "requests": requests, "exports": exports,
              "source": proof, "verified": proof["verified"], "platform_verified": False}
    assessment = assess_source(report, strict=proof)
    proof=assessment['source']
    report.update(assessment)
    report['verified']=proof['verified']
    write_json(out / "evidence.json", report)
    if target:
        from platform_verify import verify_platform
        try:
            target = (dashboard_sessions or DashboardSessions()).refresh(target)
            platform = verify_platform(report, target, poll_seconds=10)
        except Exception as error:
            platform = {"verified": False, "checks": {}, "failures": ["platform.readback_exception"],
                        "error_type": type(error).__name__, "error": str(error)}
        report.update(platform=platform, platform_verified=platform["verified"])
        report["verified"] &= platform["verified"]
        report['accepted'] &= platform['verified']
        if not report['accepted']: report['status'] = 'failed'
    write_json(out / "evidence.json", report)
    return {key: report[key] for key in ("case", "version", "verified", "platform_verified", "accepted", "status",
        "canonical_verified", "provider_reconciled", "known_limitations")} | {
        "source_verified": proof["verified"], "failures": proof["failures"] + report.get("platform", {}).get("failures", []),
        "evidence": str(out / "evidence.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtimes", type=Path, required=True, help="JSON app -> official CLI argv prefix")
    parser.add_argument("--apps", default=",".join(APPS))
    parser.add_argument("--scenarios", help="Explicit common scenarios, or OpenCode-only api-retry; default includes all supported fake cases")
    parser.add_argument("--targets", type=Path, help="Private runner-local platform targets; never committed")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--mode", choices=("fake", "nvidia"), default="fake")
    parser.add_argument("--model", default="native-e2e-chat")
    parser.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    parser.add_argument("--proxy", help="Optional explicit loopback HTTP proxy for NVIDIA")
    args = parser.parse_args(argv)
    apps = selected(args.apps, APPS)
    selected_cases = choose_cases(apps, args.scenarios, mode=args.mode)
    repo, out = args.repo.resolve(), args.out.resolve()
    if out.is_relative_to(repo):
        raise ValueError("Place native validation artifacts outside the Git checkout")
    if not 0 < args.timeout <= 1800:
        raise ValueError("Bound the case timeout to 1..1800 seconds")
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh artifact directory; existing evidence is never replaced")
    out.mkdir(parents=True, exist_ok=True)
    runtimes = load_runtimes(args.runtimes, apps)
    targets = load_targets(json.loads(args.targets.read_text()), apps) if args.targets else {}
    live_provider = None
    if args.mode == "nvidia":
        from nvidia_provider import NvidiaProvider, RateBudget
        key = os.environ.get(args.api_key_env)
        if not key or args.model == "native-e2e-chat":
            raise ValueError("Live NVIDIA requires an explicit model and key environment variable")
        PRIVATE.add(key)
        budget = RateBudget()
        live_provider = lambda: NvidiaProvider(key, budget, proxy=args.proxy, timeout=args.timeout)
    expected = {app + "-" + scenario for app, scenario in selected_cases}
    cases = []
    dashboard_sessions = DashboardSessions()
    for app, scenario in selected_cases:
        case_out = out / (app + "-" + scenario)
        try:
            row = execute_case(repo, case_out, app, scenario, runtimes[app],
                               target=targets.get(app), timeout=args.timeout, mode=args.mode,
                               model=args.model, live_provider=live_provider, dashboard_sessions=dashboard_sessions)
        except Exception as error:
            row = {"case": app + "-" + scenario, "verified": False, "source_verified": False,
                   "platform_verified": False, "error_type": type(error).__name__, "error": str(error)}
            write_json(case_out / "error.json", row)
        cases.append(row)
        print(json.dumps(safe(row), ensure_ascii=False), flush=True)
        write_json(out / "summary.json", {"status": acceptance_status(cases, expected), "mode": args.mode,
            "scope": "full" if args.mode == "fake" and set(selected_cases) == set(DEFAULT_CASES) else "selected",
            "expected_cases": len(expected), "executed_cases": len(cases),
            "verified_cases": sum(row.get('verified') is True for row in cases),
            "known_limitation_cases": sum(row.get('accepted') is True and row.get('status') == 'passed_with_known_limitations' for row in cases),
            "failed_cases": sum(row.get('verified') is not True and row.get('accepted') is not True for row in cases), "cases": cases})
    return 0 if acceptance_status(cases, expected) in ("passed", "passed_with_known_limitations") else 1


if __name__ == "__main__":
    sys.exit(main())
