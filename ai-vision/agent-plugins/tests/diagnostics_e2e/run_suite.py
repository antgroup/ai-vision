"""Native Codex/DSH -> OceanBase -> diagnosis acceptance (explicit local run)."""
from contextlib import ExitStack
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid

DIRECTORY = Path(__file__).resolve().parent
NATIVE = DIRECTORY.parent / "native_agent_e2e"
sys.path.insert(0, str(NATIVE))
from capture import Capture
from codex_provider import CodexProvider
from drivers.codex import CodexContext, codex_container_runtime
from drivers import codex, dsh
from models import CaseContext
from nvidia_provider import NvidiaProvider, RateBudget
sys.path.insert(0, str(DIRECTORY))
from docker_judge import create_judge
from providers import FakeDiagnosticProvider, FakeJudge
from judge import (create_connection, test_connection, create_diagnoser,
                   validate_judge_records, CASE_EXPECTATIONS, REASONING_EFFORTS)
from scenarios import scenarios, prepare_workspace, continue_workspace
from source import source_proof, expectations, rule_evidence_expectations, validate_targets, validate_projection
from platform_client import PlatformClient, validate_diagnosis
from credentials import RenewingIngestHeaders
from calibrate import provider_lease
from corpus import _outside_git

spec = importlib.util.spec_from_file_location("_diagnostic_native_sessions", NATIVE / "run_suite.py")
native_sessions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_sessions)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def error_details(error):
    """Keep grouped invocation/cleanup causes in durable, JSON-safe evidence."""
    details = {"error_type": type(error).__name__, "error": str(error)}
    if isinstance(error, BaseExceptionGroup):
        details["errors"] = [error_details(member) for member in error.exceptions]
    return details


def native_case(*, repo, out, artifacts, app, scenario, runtime, target=None,
                hook_binary=None, image=None, mode="fake", model="native-e2e-chat",
                live_factory=None, timeout=180, existing_workspace=None):
    out.mkdir(parents=True, exist_ok=False)
    workspace = (continue_workspace(existing_workspace, scenario) if existing_workspace is not None
        else prepare_workspace(out / "workspace", scenario))
    target = native_sessions.refresh_ingest(target) if target else None
    before = time.time_ns()
    with ExitStack() as stack:
        provider = stack.enter_context(live_factory() if live_factory else FakeDiagnosticProvider(scenario, workspace))
        capture = stack.enter_context(Capture(target.get("collector_endpoint") if target else None,
            target.get("collector_headers") if target else None,
            forward_headers_factory=RenewingIngestHeaders(target, native_sessions.refresh_ingest)
                if target else None))
        url = provider.url
        if app == "codex":
            bridge = stack.enter_context(CodexProvider(url, timeout=timeout,
                default_max_tokens=4096 if mode == "nvidia" else None))
            url = bridge.url
            if image:
                runtime = codex_container_runtime(image, repo, artifacts, workspace)
        resource = {"ant.agent.type": "codex" if app == "codex" else "dsh",
            "ant.agent.product": "Codex" if app == "codex" else "DeepSeekHarness",
            "ant.agent.instance": "diagnostics-e2e-" + uuid.uuid4().hex,
            "ant.username": "diagnostics-e2e"}
        context_class = CodexContext if app == "codex" else CaseContext
        kwargs = {"hook_binary": hook_binary, "cancel_ready": bridge.request_started} if app == "codex" else {}
        ctx = context_class(repo=repo, out=out / "native", workspace=workspace, runtime=tuple(runtime),
            provider_url=url, collector_url=capture.url, scenario=scenario.name, prompt=scenario.prompt,
            app=app, runtime_version="0.161.0" if app == "codex" else "0.1.7-rc.2"
                if app == "dsh01" else "0.2.0-rc.2", mode=mode, model=model,
            resource=resource, timeout_seconds=timeout, **kwargs)
        try:
            result = asdict((codex if app == "codex" else dsh).run_case(ctx))
        except Exception as error:
            result = {"exit_code": None, "details": error_details(error)}
        time.sleep(.25)
    # All handlers have shut down before the immutable evidence snapshot.
    requests, exports = deepcopy(provider.requests), deepcopy(capture.exports)
    proof = source_proof(result=result, exports=exports, requests=requests, scenario=scenario,
                         mode=mode, workspace=workspace, bounds=(before, time.time_ns()), resource=resource)
    report = {"app": app, "scenario": scenario.name, "mode": mode, "model": model,
        "workspace": str(workspace),
        "runtime_os": "Linux Docker" if app == "codex" and image else sys.platform,
        "result": result, "requests": requests, "exports": exports, "source": proof,
        "run_id": uuid.uuid4().hex}
    if app == "codex": report["responses_bridge"] = deepcopy(bridge.requests)
    write_json(out / "source.json", report)
    return report


def diagnose_case(client, report, scenario, out, *, live_factory=None, judge_models=(), judge_controls=None):
    if not report["source"]["verified"]:
        return {"verified": False, "failures": report["source"]["failures"]}
    traces = set(report["source"]["source_trace_ids"])
    spans = set(report["source"]["source_span_ids"])
    rules = client.builtin_rules()
    name = report["app"] + " " + scenario.name + " " + report["run_id"][:12]
    dataset = client.create_dataset(name, traces)
    job = client.create_job(name, dataset, rules)
    # Query projection first: diagnosis must consume the actually ingested trace.
    projections = {}
    for trace_id in traces:
        deadline = time.monotonic() + 90
        while True:
            try:
                projections[trace_id] = client.scoped("/trace/" + trace_id + "/projection", agent=False,
                    params={"job_uuid": job["uuid"], "center_time": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                break
            except RuntimeError:
                if time.monotonic() >= deadline: raise
                time.sleep(1)
    task = client.execute(job)
    expected, skips = expectations(report["app"], scenario, mode=report.get('mode', 'fake'))
    records = client.records(job, task, expected_count=(len(rules) - len(set(rules) & set(skips))) * len(traces))
    proof = validate_diagnosis(task, records, rules, source_trace_ids=traces, source_span_ids=spans,
        expected=expected, expected_skips=skips,
        expected_evidence={key: dict.fromkeys(traces, evidence)
            for key, evidence in rule_evidence_expectations(scenario).items() if key in rules}
            if report.get('mode') != 'nvidia' else {})
    projection_proofs = {trace_id: validate_projection(p, report, scenario) for trace_id, p in projections.items()}
    proof["verified"] &= all(p["verified"] for p in projection_proofs.values())
    proof["failures"] += [f for p in projection_proofs.values() for f in p["failures"]]
    dashboard = client.dashboard_stats(job, task, rules)
    proof["verified"] &= dashboard["verified"]
    proof["failures"] += dashboard["failures"]
    result = {**proof, "dataset": dataset, "job": job, "task": task, "records": records,
        "projections": projections, "projection_proofs": projection_proofs, "dashboard": dashboard, "expected": expected,
        "expected_skips": skips}
    bad = next((r for r in records if r["verdict"] == "bad"), None)
    if bad:
        result["feedback"] = client.feedback(bad)
    if scenario.name in {"code-change-tested", "code-change-untested"}:
        result["badcase_storage"] = verify_badcase_storage(client, name, dataset, rules, task, records)
        result["verified"] &= result["badcase_storage"]["verified"]
    write_json(out / "rule-diagnosis.json", {**result, "stage": "builtin_rules"})
    if report["mode"] == "fake" and scenario.name in CASE_EXPECTATIONS:
        result["judges"] = [evaluate_judge(client, report, scenario, name, dataset, mode=mode, projections=projections, out=out / ("judge-" + mode))
                            for mode in ("normal", "malformed", "http_failure")]
        result["verified"] &= all(j["verified"] for j in result["judges"])
    elif live_factory and scenario.name in {"code-change-tested", "code-change-untested"}:
        result["judges"] = [evaluate_judge(client, report, scenario, name + " " + str(i), dataset,
            live_factory=live_factory, judge_model=model, judge_controls=judge_controls,
            projections=projections, out=out / ("judge-" + str(i))) for i, model in enumerate(judge_models)]
        result["verified"] &= bool(result["judges"]) and all(j["verified"] for j in result["judges"])
    if client.target.get("isolation_space_id"):
        response = client.request("GET", "/api/v1/spaces/" + client.target["isolation_space_id"]
            + "/diagnose/jobs/" + job["uuid"], allowed=(403, 404))
        result["cross_space_denied"] = True
    write_json(out / "diagnosis.json", result)
    return result


def verify_badcase_storage(client, name, dataset, rules, original_task, records):
    job = client.create_job(name + " default storage", dataset, rules, save_badcase_only=None)
    if job.get("spec", {}).get("save_badcase_only", True) is not True:
        raise AssertionError("Default storage must only retain BAD records")
    task = client.execute(job)
    expected = [r for r in records if r["verdict"] == "bad"]
    dashboard = client.dashboard_stats(job, task, rules)
    # Stats publication and a second read prevent an immediately empty query
    # from being mistaken for evidence of the default storage policy.
    time.sleep(3)
    stored = client.records(job, task, expected_count=len(expected))
    def record_key(record):
        # The second task gets new record/job/task IDs and timestamps. Its
        # retained trace, metric facts and execution result must stay identical.
        facts = {key: record.get(key) for key in ("target_id", "target_type", "diagnoser_uuid",
            "diagnoser_version", "verdict", "summary", "execution")}
        facts["metrics"] = sorted(json.dumps(metric, sort_keys=True, ensure_ascii=False)
            for metric in (record.get("metrics") or []))
        return json.dumps(facts, sort_keys=True, ensure_ascii=False)
    checks = {"default_bad_only": all(r["verdict"] == "bad" for r in stored)
        and {r["diagnoser_uuid"] for r in stored} == {r["diagnoser_uuid"] for r in expected},
        "same_bad_facts": Counter(map(record_key, stored)) == Counter(map(record_key, expected)),
        "all_verdicts_counted": task.get("stats") == original_task.get("stats"),
        "dashboard": dashboard["verified"]}
    return {"verified": all(checks.values()), "checks": checks, "job": job, "task": task,
        "records": stored, "dashboard": dashboard}


def evaluate_judge(client, report, scenario, name, dataset, *, mode="normal", live_factory=None,
                   judge_model="diagnostic-fake-judge", judge_controls=None, projections, out=None):
    request_timeout = (judge_controls or {}).get("request_timeout_sec", 60)
    task_timeout = 3 * request_timeout + 3 + 60
    provider_timeout = 210 + task_timeout + 30
    provider = live_factory(timeout=provider_timeout) if live_factory else create_judge(FakeJudge)
    completed, error_type = False, None
    try:
        with provider:
            # OrbStack resolves this alias to macOS; the listener remains loopback.
            endpoint = provider.url.replace("127.0.0.1", "host.orb.internal")
            connection = create_connection(client, name=name + " judge " + mode,
                endpoint=endpoint, model_name=judge_model, api_key="diagnostic-loopback-placeholder")
            connection = test_connection(client, connection, **({"timeout": 210} if live_factory else {}))
            diagnoser = create_diagnoser(client, name=name + " judge " + mode, connection=connection,
                runtime_type="codex" if report["app"] == "codex" else "dsh", **(judge_controls or {}))
            if not live_factory: provider.mode = mode
            job = client.create_job(name + " judge " + mode, dataset, {"judge": diagnoser})
            task = client.execute(job, timeout=task_timeout if live_factory else 240)
            records = client.records(job, task, expected_count=len(report["source"]["source_trace_ids"]))
            expected = CASE_EXPECTATIONS[scenario.name]
            if mode != "normal": expected = "failed"
            accounting = validate_diagnosis(task, records, {"judge": diagnoser}, expected={},
                source_trace_ids=set(report["source"]["source_trace_ids"]),
                source_span_ids=set(report["source"]["source_span_ids"]))
            dashboard = client.dashboard_stats(job, task, {"judge": diagnoser})
        completed = True
    except BaseException as error:
        error_type = type(error).__name__
        raise
    finally:
        # Capture after shutdown, including unavailable probes and interrupted
        # tasks. These attempts never imply a successful diagnosis.
        requests = deepcopy(provider.requests)
        if out is not None:
            write_json(out / "provider-attempts.json", {"completed": completed,
                "error_type": error_type, "requests": requests})
    semantic = validate_judge_records(records, diagnoser_uuid=diagnoser["uuid"],
        source_trace_ids=set(report["source"]["source_trace_ids"]), expected=expected,
        expected_execution="succeeded" if mode == "normal" else "failed",
        source_span_ids=set(report["source"]["source_span_ids"]), expected_materials=projections,
        provider_requests=requests, expected_failure=mode if mode != "normal" else None,
        expected_http_status=503 if mode == "http_failure" else None)
    return {"verified": semantic["verified"] and accounting["verified"] and dashboard["verified"],
        "semantic": semantic, "accounting": accounting, "dashboard": dashboard,
        "mode": mode, "connection": connection, "diagnoser": diagnoser, "job": job,
        "task": task, "records": records, "requests": requests}


def parse_reasoning_effort(value):
    if value in REASONING_EFFORTS:
        return value
    if value.isdigit() and 1 <= int(value) <= 100:
        return int(value)
    raise ValueError("Use a supported effort string or integer from 1 to 100")


def main(argv=None, *, budget=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--export-current-targets", type=Path,
        help="Private 0600 handoff of current rotated dashboard cookies for a sequential runner")
    parser.add_argument("--capture-only", action="store_true", help="Preflight only; never claims platform diagnosis")
    parser.add_argument("--apps", nargs="+", choices=("codex", "dsh01", "dsh02"), default=["codex", "dsh02"])
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--codex-image")
    parser.add_argument("--hook-binary", type=Path)
    parser.add_argument("--dsh-node", type=Path)
    parser.add_argument("--dsh01", type=Path)
    parser.add_argument("--dsh02", type=Path)
    parser.add_argument("--mode", choices=("fake", "nvidia"), default="fake")
    parser.add_argument("--model", default="native-e2e-chat")
    parser.add_argument("--judge-models", nargs="+")
    parser.add_argument("--judge-reasoning-effort", type=parse_reasoning_effort)
    parser.add_argument("--judge-max-tokens", type=int)
    parser.add_argument("--judge-timeout-seconds", type=float)
    parser.add_argument("--include-long", action="store_true")
    parser.add_argument("--extended", action="store_true", help="Include incorrect repair, false reporting and failed verification")
    args = parser.parse_args(argv)
    if args.judge_max_tokens is not None and args.judge_max_tokens <= 0:
        parser.error("--judge-max-tokens must be positive")
    if args.judge_timeout_seconds is not None and (not math.isfinite(args.judge_timeout_seconds)
            or not 0 < args.judge_timeout_seconds <= 300):
        parser.error("--judge-timeout-seconds must be finite and between 0 and 300")
    judge_controls = {key: value for key, value in {
        "reasoning_effort": args.judge_reasoning_effort, "max_tokens": args.judge_max_tokens,
        "request_timeout_sec": args.judge_timeout_seconds}.items() if value is not None}
    if args.mode == "nvidia":
        # NVIDIA rejects the vendor-specific enable_thinking parameter.
        judge_controls["enable_thinking"] = None
    args.repo, args.out = args.repo.resolve(), args.out.resolve()
    try:
        _outside_git(args.out)
        if args.artifacts: _outside_git(args.artifacts)
    except ValueError:
        parser.error("Source output/artifacts require private paths outside every checkout")
    if args.export_current_targets:
        try:
            args.export_current_targets = _outside_git(args.export_current_targets)
        except ValueError:
            parser.error("Current targets require a private path outside every checkout")
        if not args.targets or args.export_current_targets.exists():
            parser.error("Current targets require --targets and a new private path outside the checkout")
    if not args.targets and not args.capture_only:
        parser.error("Supply a local OceanBase --targets file or explicitly choose --capture-only")
    if args.mode == "nvidia" and os.environ.get("GITHUB_ACTIONS"):
        parser.error("NVIDIA diagnostics are explicitly local")
    if args.mode == "nvidia" and not os.environ.get("NVIDIA_API_KEY"):
        parser.error("NVIDIA_API_KEY must be supplied privately")
    if args.mode == "nvidia" and args.model == "native-e2e-chat":
        parser.error("A current NVIDIA --model identifier is required")
    args.out.mkdir(parents=True, exist_ok=False)
    selected = [s for s in scenarios(args.include_long, include_extended=args.extended, include_rag=bool(args.scenarios and any(name.startswith("rag-") for name in args.scenarios))) if not args.scenarios or s.name in args.scenarios]
    if not selected or (args.scenarios and set(args.scenarios) != {s.name for s in selected}):
        parser.error("Unknown or empty diagnostic scenarios")
    if args.mode == "nvidia" and any(s.name not in {"read-only", "code-change-tested", "code-change-untested", "rag-grounded", "rag-insufficient-evidence"} for s in selected):
        parser.error("Live models use the bounded read/repair/RAG corpus")
    if any(s.name.startswith("rag-") for s in selected) and any(app not in {"codex", "dsh02"} for app in args.apps):
        parser.error("RAG source scenes support Codex and DSH 0.2")
    targets = validate_targets(json.loads(args.targets.read_text()), args.apps) if args.targets else {}
    if args.mode == "nvidia":
        key = os.environ["NVIDIA_API_KEY"]
        with provider_lease(key, budget) as lease:
            return _run_cases(args, selected, targets, lease, key, judge_controls)
    return _run_cases(args, selected, targets, budget or RateBudget(), None, judge_controls)


def _owned_rotation(original, current, app, rotated):
    before = original[app]
    if (not isinstance(rotated, dict) or set(rotated) != set(before)
            or any(rotated.get(field) != before.get(field) for field in (
                "endpoint", "collector_endpoint", "data_backend", "agent_product",
                "space_id", "space_key", "agent_uuid", "isolation_space_id"))
            or any(not isinstance(rotated.get(field), dict) for field in (
                "dashboard_headers", "collector_headers") if field in before)):
        raise ValueError("Current session changed owned target identity")
    current[app] = deepcopy(rotated)


def _export_owned_targets(path, original, current):
    _outside_git(path)
    if set(current) != set(original):
        raise ValueError("Current session target inventory changed")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(current, stream)


def _sync_cached_owned_sessions(original, current, sessions):
    """Publish validated shared-session rotations already held in memory."""
    cache = getattr(sessions, "sessions", None)
    if not isinstance(cache, dict):
        return
    for app, target in current.items():
        headers = target.get("dashboard_headers", {})
        cookie = headers.get("Cookie") if isinstance(headers, dict) else None
        if not isinstance(cookie, str):
            continue
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
        except CookieError:
            continue
        refresh = parsed.get("aivision_refresh")
        if refresh is None:
            continue
        state = cache.get((target["endpoint"].rstrip("/"), refresh.value))
        if state is None:
            continue
        values = state.get("cookies") if isinstance(state, dict) else None
        if (not isinstance(values, dict) or not values.get("aivision_refresh")
                or not values.get("aivision_access")
                or any(not isinstance(key, str) or not isinstance(value, str)
                    for key, value in values.items())):
            raise ValueError("Invalid cached owned dashboard session")
        latest = {**target, "dashboard_headers": {**headers,
            "Cookie": "; ".join(key + "=" + value for key, value in values.items())}}
        _owned_rotation(original, current, app, latest)


def _run_cases(args, selected, targets, budget, key, judge_controls):
    sessions = native_sessions.DashboardSessions()
    live_factory = (lambda timeout=600: NvidiaProvider(key, budget,
        proxy=os.environ.get("NVIDIA_PROXY"), timeout=timeout)) if args.mode == "nvidia" else None
    results = []
    current = deepcopy(targets)
    interrupted = None
    try:
        for app in args.apps:
            runtime = (str(args.dsh_node), str(getattr(args, app))) if app.startswith("dsh") else ("codex",)
            for scenario in selected:
                case_out = args.out / (app + "-" + scenario.name)
                try:
                    target = current.get(app)
                    if target:
                        target = sessions.refresh(target)
                        _owned_rotation(targets, current, app, target)
                    report = native_case(repo=args.repo, out=case_out, artifacts=(args.artifacts or args.out).resolve(),
                        app=app, scenario=scenario, runtime=runtime, target=target,
                        hook_binary=args.hook_binary, image=args.codex_image, mode=args.mode, model=args.model,
                        live_factory=live_factory,
                        timeout=1620 if scenario.name == "long-latency" else 600 if args.mode == "nvidia" else 180)
                    diagnosis = {"verified": False, "failures": [] if args.capture_only else ["platform_not_requested"]}
                    if target and not args.capture_only:
                        # Long native runs outlive the access cookie. Renew using
                        # this runner's rotated refresh token before API diagnosis.
                        target = sessions.refresh(target)
                        _owned_rotation(targets, current, app, target)
                        with PlatformClient(target["endpoint"], target=target, dashboard_sessions=sessions) as client:
                            try:
                                diagnosis = diagnose_case(client, report, scenario, case_out,
                                    live_factory=live_factory, judge_models=args.judge_models or [args.model], judge_controls=judge_controls)
                            finally:
                                _owned_rotation(targets, current, app, client.target)
                    row = {"case": app + "-" + scenario.name, "source_verified": report["source"]["verified"],
                        "diagnosis_verified": diagnosis["verified"],
                        "verified": report["source"]["verified"] and (diagnosis["verified"] or args.capture_only),
                        "failures": report["source"]["failures"] + diagnosis.get("failures", [])}
                except Exception as error:
                    row = {"case": app + "-" + scenario.name, "verified": False,
                        **error_details(error)}
                results.append(row)
                print(json.dumps(row), flush=True)
                write_json(args.out / "summary.json", {"verified": len(results) == len(args.apps) * len(selected)
                    and all(r["verified"] for r in results),
                    "completed": len(results), "expected": len(args.apps) * len(selected), "cases": results,
                    "provider_attempts": budget.attempts, "stage": "native_capture" if args.capture_only else "platform_diagnosis"})
    except BaseException as error:
        interrupted = error
        raise
    finally:
        if args.export_current_targets:
            try:
                _sync_cached_owned_sessions(targets, current, sessions)
                _export_owned_targets(args.export_current_targets, targets, current)
            except BaseException:
                if interrupted is None:
                    raise
    return 0 if all(r["verified"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
