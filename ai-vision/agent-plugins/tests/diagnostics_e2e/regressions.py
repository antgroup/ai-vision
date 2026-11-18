"""Native-source quality matrix and persistent-workspace repair acceptance."""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import time
import uuid

from judge import CASE_EXPECTATIONS, METRIC_NAMES, create_connection, test_connection, diagnoser_payload, validate_judge_records
from platform_client import PlatformClient, trace_filter, validate_diagnosis
from docker_judge import create_judge
from providers import FakeJudge
from run_suite import native_case, native_sessions, write_json
from scenarios import scenarios
from source import expectations, rule_evidence_expectations, validate_projection, validate_targets


def validate_repair_history(*, before_task, after_task, before_point, points,
        before_records, old_records, old_task, before_traces, after_traces, expected_after):
    old = [p for p in points if p.get("task_uuid") == before_task["uuid"]]
    new = [p for p in points if p.get("task_uuid") == after_task["uuid"]]
    checks = {
        "different_tasks": before_task["uuid"] != after_task["uuid"],
        "new_source": bool(before_traces) and bool(after_traces) and not before_traces.intersection(after_traces),
        "old_task_unchanged": old_task == before_task,
        "old_records_unchanged": Counter(json.dumps(r, sort_keys=True) for r in old_records)
            == Counter(json.dumps(r, sort_keys=True) for r in before_records),
        "old_quality_unchanged": len(old) == 1 and old[0] == before_point,
        "after_quality": len(new) == 1 and new[0].get("global", {}).get("score") == expected_after,
        "different_periods": len(old) == len(new) == 1 and old[0].get("time") != new[0].get("time"),
    }
    return {"verified": all(checks.values()), "checks": checks,
        "failures": [k for k, v in checks.items() if not v]}


def _group(client, name, connection, runtime, *, metrics=METRIC_NAMES, category="outcome", weight=1):
    payload = diagnoser_payload(name, connection["uuid"], runtime_type=runtime)
    payload["spec"].update(category=category, weight=weight,
        metrics=[m for m in payload["spec"]["metrics"] if m["name"] in metrics])
    created = client.scoped("/diagnosers", "POST", body=payload)
    result = client.scoped("/diagnosers/" + created["uuid"], agent=False)
    if (result.get("type") != "custom" or result.get("mode") != "llm"
            or result.get("name") != name or any(result.get("spec", {}).get(k) != v for k, v in payload["spec"].items())):
        raise AssertionError("Independent Judge configuration did not survive public read-back")
    return result


def _projection(client, report, scenario, job):
    result = {}
    for trace in report["source"]["source_trace_ids"]:
        deadline = time.monotonic() + 90
        while True:
            try:
                result[trace] = client.scoped("/trace/" + trace + "/projection", agent=False,
                    params={"job_uuid": job["uuid"], "center_time": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        proof = validate_projection(result[trace], report, scenario)
        if not proof["verified"]:
            raise AssertionError("Native projection mismatch: " + str(proof["failures"]))
    return result


def _execute(client, job, diagnosers, sources, providers):
    state = {"job": job, "phase": "projection"}
    try:
        return _execute_inner(client, job, diagnosers, sources, providers, state)
    except BaseException as error:
        if hasattr(error, "task"):
            state["task"] = error.task
        error.diagnostic_evidence = state
        raise


def _execute_inner(client, job, diagnosers, sources, providers, state):
    traces = {trace for report, _ in sources for trace in report["source"]["source_trace_ids"]}
    spans = {span for report, _ in sources for span in report["source"]["source_span_ids"]}
    if len(traces) != len(sources) or not all(report["source"]["verified"] for report, _ in sources):
        raise AssertionError("Quality matrix requires unique, independently verified native traces")
    projections = {}
    for report, scenario in sources:
        projections.update(_projection(client, report, scenario, job))
    state.update(projections=projections, phase="task_execution")
    task = client.execute(job)
    state.update(task=task, phase="records")
    skip_targets = {}
    evidence_targets = {}
    for report, scenario in sources:
        _, skips = expectations(report["app"], scenario)
        for key, reason in skips.items():
            if key in diagnosers:
                skip_targets.setdefault(key, {}).update(dict.fromkeys(report["source"]["source_trace_ids"], reason))
        for key, evidence in rule_evidence_expectations(scenario).items():
            if key in diagnosers:
                evidence_targets.setdefault(key, {}).update(dict.fromkeys(report["source"]["source_trace_ids"], evidence))
    records = client.records(job, task, expected_count=len(diagnosers) * len(traces)
        - sum(len(targets) for targets in skip_targets.values()))
    state.update(records=records, phase="statistics")
    accounting = validate_diagnosis(task, records, diagnosers, source_trace_ids=traces,
        source_span_ids=spans, expected={}, expected_skips=skip_targets, expected_evidence=evidence_targets)
    proofs = [accounting]
    for report, scenario in sources:
        source_ids = set(report["source"]["source_trace_ids"])
        selected = [r for r in records if r["target_id"] in source_ids]
        expected_rules, skips = expectations(report["app"], scenario)
        for key, diagnoser in diagnosers.items():
            if key in skips:
                continue  # Counters, exact reasons and absence of Records are checked above.
            if key in expected_rules:
                facts = [m for r in selected if r["diagnoser_uuid"] == diagnoser["uuid"] for m in r["metrics"]]
                passed = (len(facts) == len(source_ids) and all(m["verdict"] == expected_rules[key]
                    and (m.get("evidence") or {}).get("exempted") is not True for m in facts))
                proofs.append({"verified": passed, "failures": [] if passed else ["rule:" + key + ":" + scenario.name]})
                continue
            names = tuple(m["name"] for m in diagnoser["spec"]["metrics"])
            provider = providers[key]
            expected = CASE_EXPECTATIONS.get(scenario.name, dict.fromkeys(METRIC_NAMES, "good"))
            failed = provider.mode != "normal"
            proofs.append(validate_judge_records(selected, diagnoser_uuid=diagnoser["uuid"],
                source_trace_ids=source_ids, source_span_ids=set(report["source"]["source_span_ids"]),
                metric_names=names, expected={m: "failed" if failed else expected[m] for m in names},
                expected_execution="failed" if failed else "succeeded", expected_materials=projections,
                provider_requests=deepcopy(provider.requests), expected_failure=provider.mode if failed else None,
                expected_http_status=503 if provider.mode == "http_failure" else None))
    dashboard = client.dashboard_stats(job, task, diagnosers)
    proofs.append(dashboard)
    point = next((p for p in dashboard["response"].get("tasks", []) if p["task_uuid"] == task["uuid"]), None)
    return {"verified": all(p["verified"] for p in proofs), "failures": [f for p in proofs for f in p.get("failures", [])],
        "job": job, "task": task, "records": records, "projections": projections, "dashboard": dashboard,
        "point": point, "source_trace_ids": sorted(traces), "proofs": proofs}


def quality_matrix(client, sources, app, out):
    out.mkdir(parents=True, exist_ok=False)
    providers, results, state = {}, [], {}
    try:
        return _quality_matrix(client, sources, app, out, providers, results, state)
    except BaseException as error:
        if out.exists():
            failed = {"verified": False, "failures": ["execution_exception"],
                "error_type": type(error).__name__, **state,
                "execution": getattr(error, "diagnostic_evidence", {})}
            if "profile" in state:
                write_json(out / (state["profile"] + ".json"), failed)
            write_json(out / "summary.json", {"verified": False, "profiles": results,
                "expected": 10, "completed": len(results), "error_type": type(error).__name__,
                "failed_profile": state.get("profile")})
        raise
    finally:
        if out.exists():
            write_json(out / "provider-attempts.json", {mode: deepcopy(p.requests) for mode, p in providers.items()})


def _quality_matrix(client, sources, app, out, providers, results, state):
    prefix = app + " quality " + uuid.uuid4().hex[:8]
    runtime = "codex" if app == "codex" else "dsh"
    with ExitStack() as stack:
        connections = {}
        for mode in ("normal", "malformed", "http_failure"):
            provider = stack.enter_context(create_judge(FakeJudge))
            providers[mode] = provider
            connection = create_connection(client, name=prefix + " " + mode,
                endpoint=provider.url.replace("127.0.0.1", "host.orb.internal"),
                model_name="diagnostic-fake-judge", api_key="diagnostic-loopback-placeholder")
            connections[mode] = test_connection(client, connection)
            provider.mode = mode
            providers[mode] = provider
        group = _group(client, prefix + " composite", connections["normal"], runtime)
        single = {key: _group(client, prefix + " " + key, connections["normal"], runtime,
            metrics=(key,), category="process" if key == "test_discipline" else "outcome",
            weight=3 if key == "task_completion" else 1) for key in METRIC_NAMES}
        failed = {mode: _group(client, prefix + " " + mode, connections[mode], runtime)
                  for mode in ("malformed", "http_failure")}
        profiles = [
            ("score-0", ("incomplete-repair",), {"composite": group}, 0),
            ("score-33.3", ("misreported-verification",), {"composite": group}, 33.3),
            ("score-66.7", ("code-change-untested",), {"composite": group}, 66.7),
            ("score-100", ("code-change-tested",), {"composite": group}, 100),
            ("cohort-50", ("incomplete-repair", "code-change-tested"), {"composite": group}, 50),
            ("cohort-75", ("incomplete-repair", "code-change-tested", "repair-with-verification", "rewrite-overlap"), {"composite": group}, 75),
            ("weighted-75", ("misreported-verification",), {k: single[k] for k in ("task_completion", "report_fidelity")}, 75),
            ("failed-only", ("code-change-tested",), failed, None),
            ("mixed-failure", ("code-change-untested",), {"composite": group, **failed}, None),
            ("joint-13-diagnosers", ("misreported-verification",), {**client.builtin_rules(), **single, **failed}, "independent"),
        ]
        for label, names, diagnosers, expected_score in profiles:
            state.clear()
            state.update(profile=label, phase="configuration", diagnosers=diagnosers)
            selected = [sources[name] for name in names]
            traces = {trace for report, _ in selected for trace in report["source"]["source_trace_ids"]}
            dataset = client.create_dataset(prefix + " " + label, traces)
            job = client.create_job(prefix + " " + label, dataset, diagnosers)
            state.update(job=job, phase="diagnosis")
            bindings = {key: providers[key] if key in failed else providers["normal"]
                        for key in diagnosers if key not in client.builtin_rules()}
            result = _execute(client, job, diagnosers, selected, bindings)
            actual = result["point"]["global"]["score"] if result["point"] else "missing"
            if expected_score != "independent" and actual != expected_score:
                result["verified"] = False; result["failures"].append("literal_quality_score")
            result.update(profile=label, expected_score=expected_score, actual_score=actual, diagnosers=diagnosers)
            write_json(out / (label + ".json"), result)
            results.append({"profile": label, "verified": result["verified"], "score": actual, "job_uuid": job["uuid"],
                "task_uuid": result["task"]["uuid"], "failures": result["failures"]})
            print(json.dumps({"app": app, **results[-1]}), flush=True)
    summary = {"verified": all(r["verified"] for r in results), "profiles": results, "expected": len(profiles), "completed": len(results)}
    write_json(out / "summary.json", summary)
    return summary


def repair_workflow(client, **kwargs):
    state, providers, result = {"phase": "configuration"}, [], None
    out = kwargs["out"]
    out.mkdir(parents=True, exist_ok=False)
    try:
        result = _repair_workflow(client, _state=state, _providers=providers, **kwargs)
        return result
    except BaseException as error:
        result = {"verified": False, "failures": ["execution_exception"],
            "error_type": type(error).__name__, **state,
            "execution": getattr(error, "diagnostic_evidence", {}),
            "with_verification": kwargs.get("with_verification", True)}
        raise
    finally:
        if out.exists():
            requests = deepcopy(providers[0].requests) if providers else []
            write_json(out / "provider-attempts.json", {"completed": bool(result and result.get("verified")),
                "requests": requests})
            if result is not None:
                result["provider_requests"] = requests
                write_json(out / "comparison.json", result)


def _repair_workflow(client, *, before, repair, app, repo, out, runtime, artifacts, image=None, hook_binary=None, with_verification=True, _state, _providers):
    report, scenario = before
    prefix = app + " repair " + uuid.uuid4().hex[:8]
    with create_judge(FakeJudge) as provider:
        _providers.append(provider)
        connection = test_connection(client, create_connection(client, name=prefix,
            endpoint=provider.url.replace("127.0.0.1", "host.orb.internal"), model_name="diagnostic-fake-judge",
            api_key="diagnostic-loopback-placeholder"))
        group = _group(client, prefix, connection, "codex" if app == "codex" else "dsh")
        diagnosers = {"composite": group, "test_execution": client.builtin_rules()["test_execution"]}
        dataset = client.create_dataset(prefix, report["source"]["source_trace_ids"])
        job = client.create_job(prefix, dataset, diagnosers)
        _state.update(job=job, phase="before_diagnosis")
        old = _execute(client, job, diagnosers, [before], {"composite": provider})
        _state["before"] = old
        write_json(out / "before.json", old)
        if old["point"]["global"]["score"] != 0:
            raise AssertionError("Broken, unverified before trace must score zero")
        if not with_verification:
            repair = replace(repair, name="code-change-untested", tool_steps=repair.tool_steps[:-1],
                expected_rules={**repair.expected_rules, "test_execution": "bad"},
                prompt="Repair the remaining addition error and read back app.py. Do not execute tests.")
        workspace = Path(report["workspace"])
        _state["phase"] = "native_repair"
        repaired = native_case(repo=repo, out=out / "after-native", artifacts=artifacts, app=app,
            scenario=repair, runtime=runtime, target=client.target, hook_binary=hook_binary, image=image,
            existing_workspace=workspace)
        _state["after_source"] = repaired
        if not repaired["source"]["verified"]:
            raise AssertionError("Repair did not produce valid native evidence: " + str(repaired["source"]["failures"]))
        after_ids = set(repaired["source"]["source_trace_ids"])
        _state["phase"] = "dataset_update"
        updated = client.scoped("/datasets/" + dataset["uuid"], "PATCH", agent=False,
            body={"spec": {"query": {"where": trace_filter(after_ids)}}})
        latest = client.scoped("/datasets/" + dataset["uuid"], agent=False)
        if latest["uuid"] != dataset["uuid"] or latest["spec"]["query"]["where"] != updated["spec"]["query"]["where"]:
            raise AssertionError("Repair dataset update was not persisted")
        # Different real execution periods preserve both points under the existing
        # latest-success-per-period API contract. No timestamps are rewritten.
        time.sleep(1.1)
        _state["phase"] = "after_diagnosis"
        new = _execute(client, job, diagnosers, [(repaired, repair)], {"composite": provider})
        _state.update(after=new, phase="history_readback")
        old_task = client.scoped("/tasks/" + old["task"]["uuid"], agent=False)
        old_records = client.records(job, old["task"], expected_count=len(old["records"]))
        proof = validate_repair_history(before_task=old["task"], after_task=new["task"], before_point=old["point"],
            points=new["dashboard"]["response"]["tasks"], before_records=old["records"], old_records=old_records,
            old_task=old_task, before_traces=set(report["source"]["source_trace_ids"]), after_traces=after_ids,
            expected_after=100 if with_verification else 33.3)
        proof["verified"] &= old["verified"] and new["verified"]
        proof["failures"] += old["failures"] + new["failures"]
        result = {**proof, "before": old, "after": new, "workspace": str(workspace), "same_dataset_uuid": dataset["uuid"],
            "with_verification": with_verification, "provider_requests": deepcopy(provider.requests)}
    return result


def export_current_targets(path, targets, sessions):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({app: sessions.refresh(target) for app, target in targets.items()}, stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("repo", "out", "cases", "targets", "artifacts"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--apps", nargs="+", choices=("codex", "dsh02"), default=["codex", "dsh02"])
    parser.add_argument("--codex-image")
    parser.add_argument("--hook-binary", type=Path)
    parser.add_argument("--dsh-node", type=Path)
    parser.add_argument("--dsh02", type=Path)
    parser.add_argument("--export-current-targets", type=Path)
    args = parser.parse_args(argv)
    if args.export_current_targets is not None:
        path = args.export_current_targets
        if path.exists() or path.is_symlink() or path.resolve().is_relative_to(args.repo.resolve()):
            parser.error("Current targets export must be a new private file outside Git")
    args.out.mkdir(parents=True, exist_ok=False)
    targets = validate_targets(json.loads(args.targets.read_text()), args.apps)
    cases = {s.name: s for s in scenarios(include_extended=True)}
    sessions, results = native_sessions.DashboardSessions(), []
    try:
        for app in args.apps:
            target = sessions.refresh(targets[app])
            with PlatformClient(target["endpoint"], target=target, dashboard_sessions=sessions) as client:
                sources = {name: (json.loads((args.cases / (app + "-" + name) / "source.json").read_text()), cases[name])
                           for name in ("incomplete-repair", "misreported-verification", "code-change-untested",
                                        "code-change-tested", "repair-with-verification", "rewrite-overlap")}
                # Older capture reports precede the explicit workspace metadata;
                # the canonical case directory still owns its actual fixture files.
                for name, (report, _) in sources.items():
                    report.setdefault("workspace", str((args.cases / (app + "-" + name) / "workspace").resolve()))
                matrix = quality_matrix(client, sources, app, args.out / (app + "-quality"))
                results.append({"case": app + "-quality-matrix", "verified": matrix["verified"], "profiles": matrix["completed"]})
                runtime = (str(args.dsh_node), str(args.dsh02)) if app == "dsh02" else ("codex",)
                for verified in (True, False):
                    label = "verified-repair" if verified else "incomplete-verification"
                    stage = args.out / (app + "-" + label)
                    if verified:
                        before = sources["incomplete-repair"]
                    else:
                        report = native_case(repo=args.repo, out=args.out / (app + "-second-before"), artifacts=args.artifacts,
                            app=app, scenario=cases["incomplete-repair"], runtime=runtime, target=client.target,
                            hook_binary=args.hook_binary, image=args.codex_image)
                        before = report, cases["incomplete-repair"]
                    result = repair_workflow(client, before=before, repair=cases["repair-with-verification"],
                        app=app, repo=args.repo, out=stage, runtime=runtime, artifacts=args.artifacts,
                        image=args.codex_image, hook_binary=args.hook_binary, with_verification=verified)
                    results.append({"case": app + "-" + label, "verified": result["verified"],
                        "before_score": result["before"]["point"]["global"]["score"],
                        "after_score": result["after"]["point"]["global"]["score"], "failures": result["failures"]})
                    print(json.dumps(results[-1]), flush=True)
    finally:
        write_json(args.out / "summary.json", {"verified": len(results) == len(args.apps) * 3
            and all(r["verified"] for r in results), "cases": results,
            "expected": len(args.apps) * 3, "completed": len(results)})
    if args.export_current_targets is not None:
        export_current_targets(args.export_current_targets, targets, sessions)
    return 0 if all(r["verified"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
