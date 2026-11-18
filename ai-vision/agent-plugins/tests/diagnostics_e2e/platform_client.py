"""Browser-equivalent diagnostic API access and independent result checks."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
import re
import time
from urllib.parse import urlsplit

import httpx


def trace_filter(trace_ids):
    if not trace_ids or any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in trace_ids):
        raise ValueError("Require explicit canonical trace IDs")
    return "`parent_span_id` = '' AND `trace_id` IN (" + ",".join(
        "'" + value + "'" for value in sorted(trace_ids)) + ")"


def validate_stats(value, task, diagnosers):
    """Recompute every score from TaskStats, without using dashboard aggregates.

    Legacy tasks require the complete current configured tree. V3 tasks use their
    frozen definitions, retain visible metric counts, and require null quality
    summaries for incomplete source or quality coverage.
    """
    from math import isfinite

    categories = {"outcome": "结果质量", "process": "过程质量",
                  "safety": "安全合规", "system": "系统性能"}
    checks = {}

    def check(name, passed):
        checks[name] = bool(passed)
        return checks[name]

    def result():
        failures = [name for name, passed in checks.items() if not passed]
        return {"verified": not failures, "checks": checks, "failures": failures}

    def number(value):
        return type(value) in (int, float) and isfinite(value)

    def indexed(rows, field, expected, prefix):
        shaped = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        check(prefix + ":shape", shaped)
        if not shaped:
            return {}
        identities = [row.get(field) for row in rows]
        valid = all(isinstance(identity, str) and identity for identity in identities)
        check(prefix + ":identities", valid and len(identities) == len(set(identities))
              and set(identities) == set(expected))
        return {row[field]: row for row in rows if isinstance(row.get(field), str)}

    def score(prefix, row, ratio):
        expected = round(100 * ratio, 1) if ratio is not None else None
        actual = row.get("score")
        check(prefix + ":score", "score" in row and (actual is None if expected is None
              else number(actual) and actual == expected))

    if not check("dashboard.input", isinstance(value, dict) and isinstance(task, dict)
                 and isinstance(task.get("uuid"), str) and bool(task["uuid"])
                 and isinstance(diagnosers, dict)):
        return result()
    skeleton = indexed(value.get("categories"), "id", categories, "dashboard.definitions")
    for category, label in categories.items():
        check("dashboard.definitions:" + category + ":name", skeleton.get(category, {}).get("name") == label)
    points = value.get("tasks")
    if not check("dashboard.tasks:shape", isinstance(points, list)
                 and all(isinstance(point, dict) for point in points)):
        return result()
    identities = [point.get("task_uuid") for point in points]
    valid = all(isinstance(identity, str) and identity for identity in identities)
    check("dashboard.tasks:identities", valid and len(identities) == len(set(identities)))
    selected = [point for point in points if point.get("task_uuid") == task["uuid"]]
    if not check("dashboard.task_coverage", len(selected) == 1):
        return result()
    point = selected[0]
    check("dashboard.target_type", point.get("target_type") == "trace")
    check("dashboard.time", isinstance(point.get("time"), str) and bool(point["time"]))
    stats = task.get("stats")
    v3 = isinstance(stats, dict) and type(stats.get("schema_version")) is int and stats["schema_version"] == 3
    allowed_stats = {"source", "diagnosers", "selector"}
    if v3:
        allowed_stats |= {"schema_version", "score_policy_version", "diagnoser_definitions", "skip_reasons", "source_complete"}
    if not check("task.stats:shape", isinstance(stats, dict) and isinstance(stats.get("source"), dict)
                 and isinstance(stats.get("diagnosers"), dict)
                 and not set(stats) - allowed_stats):
        return result()
    source, stored = stats["source"], stats["diagnosers"]
    check("task.source:counts", not source or (set(source) == {"total", "invalid", "projection_failed"}
          and all(type(count) is int and count >= 0 for count in source.values())
          and source["invalid"] + source["projection_failed"] <= source["total"]))
    check("dashboard.selector", "selector" in point and point["selector"] == stats.get("selector"))

    frozen = {}
    source_complete = True
    if v3:
        check("task.stats:policy", stats.get("score_policy_version") == "lightweight-v1"
              and type(stats.get("source_complete")) is bool)
        for field in ("schema_version", "score_policy_version", "source_complete", "skip_reasons"):
            check("dashboard." + field, field in point and point[field] == stats.get(field)
                  and (type(point[field]) is type(stats.get(field)) if field in ("schema_version", "source_complete") else True))
        check("dashboard.definition_source", point.get("definition_source") == "task_snapshot")
        frozen = stats.get("diagnoser_definitions")
        reasons = stats.get("skip_reasons")
        if not check("task.definitions:shape", isinstance(frozen, dict) and isinstance(reasons, dict)):
            return result()
        public_fields = {"uuid", "key", "name", "category", "weight", "score_role", "metrics", "version", "revision", "digest"}
        optional_metadata = {"builtin_key", "canonical_variant", "prompt_version", "result_boundary"}
        snapshot_diagnosers = {}
        for uuid, entry in frozen.items():
            valid = (isinstance(uuid, str) and bool(uuid) and isinstance(entry, dict)
                and public_fields.issubset(entry) and not set(entry) - public_fields - optional_metadata)
            valid = valid and all(isinstance(entry[k], str) and len(entry[k]) <= 512
                for k in optional_metadata & set(entry))
            valid = valid and entry["uuid"] == uuid and all(isinstance(entry[k], str) and entry[k]
                for k in ("key", "name", "category", "version", "revision", "digest"))
            valid = valid and entry["score_role"] in ("quality", "signal") and number(entry["weight"]) and entry["weight"] >= 0
            valid = valid and bool(re.fullmatch(r"[0-9a-f]{64}", entry["digest"]))
            if not check(str(uuid) + ":snapshot:definition", valid):
                continue
            metrics = entry["metrics"]
            valid_metrics = isinstance(metrics, list) and bool(metrics) and all(isinstance(metric, dict)
                and not set(metric) - {"name", "desc"} and isinstance(metric.get("name"), str) and metric["name"]
                and isinstance(metric.get("desc", ""), str) for metric in metrics)
            if not check(str(uuid) + ":snapshot:metrics", valid_metrics):
                continue
            # UUID is the frozen definition identity. Executor/family keys are
            # metadata and can be shared by distinct CUSTOM or variant rows.
            snapshot_diagnosers[uuid] = {"uuid": uuid, "name": entry["name"], "spec": entry}
        check("task.skip_reasons", all(uuid in frozen and isinstance(counts, dict)
            and all(isinstance(reason, str) and reason and type(count) is int and count >= 0 for reason, count in counts.items())
            for uuid, counts in reasons.items()))
        diagnosers = snapshot_diagnosers
        selector = stats.get("selector") or {}
        check("task.selector:shape", isinstance(selector, dict))
        source_complete = (stats.get("source_complete") is True and not source.get("invalid", 0)
            and not source.get("projection_failed", 0) and isinstance(selector, dict)
            and selector.get("status", "complete") == "complete")

    definitions = {}
    for key, diagnoser in diagnosers.items():
        prefix = str(key) + ":definition"
        valid = isinstance(diagnoser, dict) and isinstance(diagnoser.get("uuid"), str)
        valid = valid and bool(diagnoser["uuid"]) and isinstance(diagnoser.get("name"), str)
        valid = valid and isinstance(diagnoser.get("spec"), dict)
        if not check(prefix + ":shape", valid):
            continue
        spec, uuid = diagnoser["spec"], diagnoser["uuid"]
        metrics = spec.get("metrics")
        valid = isinstance(metrics, list) and bool(metrics) and all(isinstance(metric, dict)
            and isinstance(metric.get("name"), str) and metric["name"] for metric in metrics)
        if not check(prefix + ":metrics", valid):
            continue
        names = [metric["name"] for metric in metrics]
        check(prefix + ":unique", uuid not in definitions and len(set(names)) == len(names))
        category = spec.get("category")
        category = category if isinstance(category, str) and category in categories else "outcome"
        weight = spec.get("weight", 0)
        weight = float(weight) if number(weight) else 0.0
        definitions[uuid] = (str(key), diagnoser, category, weight)
    empty = not stored
    check("task.diagnosers:identities", set(stored).issubset(definitions) if v3 else empty or set(stored) == set(definitions))
    coverage, ratios = set(), {}
    counter_fields = {"total", "good", "bad", "failed"}
    default_counts = {field: 0 for field in (*counter_fields, "skipped")}
    for uuid, (key, diagnoser, category, weight) in definitions.items():
        metrics = diagnoser["spec"]["metrics"]
        counters_by_name = stored.get(uuid, {})
        shaped = isinstance(counters_by_name, dict)
        metric_names = {metric["name"] for metric in metrics}
        check(key + ":stats:identities", shaped and (set(counters_by_name).issubset(metric_names) if v3
              else empty or set(counters_by_name) == metric_names))
        for metric in metrics:
            name, prefix = metric["name"], key + ":" + metric["name"]
            counters = counters_by_name.get(name) if shaped else None
            missing = counters is None
            if empty or (v3 and missing):
                counters = default_counts
            valid = isinstance(counters, dict) and counter_fields.issubset(counters)
            valid = valid and not set(counters) - counter_fields - {"skipped", "evaluate"}
            valid = valid and all(type(count) is int and count >= 0 for count in counters.values())
            valid = valid and counters["total"] == sum(counters[field] for field in ("good", "bad", "failed"))
            valid = valid and counters.get("evaluate", 0) <= counters["total"]
            check(prefix + ":stats:counts", valid)
            if valid:
                coverage.add(counters["total"] + counters.get("skipped", 0))
                judged = counters["good"] + counters["bad"]
                ratio = counters["good"] / judged if judged else None
                if v3 and (missing or counters["failed"] or counters.get("skipped", 0)
                           or ("total" in source and counters["total"] != source["total"])):
                    ratio = None
                ratios[(uuid, name)] = (counters, ratio)
            else:
                ratios[(uuid, name)] = (default_counts, None)
    check("task.stats:coverage", v3 or len(coverage) <= 1)
    expected_count = source.get("total", max(coverage, default=0)) if v3 else next(iter(coverage)) if len(coverage) == 1 else 0
    if v3:
        for identity, (counters, ratio) in list(ratios.items()):
            if counters["total"] != expected_count:
                ratios[identity] = (counters, None)
    check("dashboard.target_count", type(point.get("target_count")) is int
          and point["target_count"] == expected_count)
    global_node = point.get("global")
    if not check("dashboard.global:shape", isinstance(global_node, dict)):
        return result()
    category_rows = indexed(global_node.get("categories"), "id", categories, "dashboard.categories")
    category_ratios = []
    for category, label in categories.items():
        category_node = category_rows.get(category, {})
        check("dashboard.category:" + category + ":name", category_node.get("name") == label)
        expected = {uuid: definition for uuid, definition in definitions.items() if definition[2] == category}
        rows = indexed(category_node.get("diagnosers"), "uuid", expected, "dashboard.category:" + category)
        scored = []
        quality_incomplete = False
        for uuid, (key, diagnoser, _, weight) in expected.items():
            row = rows.get(uuid, {})
            check(key + ":name", row.get("name") == diagnoser["name"])
            check(key + ":weight", number(row.get("weight")) and row["weight"] == weight)
            if v3:
                for field in ("score_role", "version", "revision", "digest", "key"):
                    check(key + ":" + field, field in row and row[field] == frozen[uuid][field])
            metrics = diagnoser["spec"]["metrics"]
            actual = indexed(row.get("metrics"), "id", [metric["name"] for metric in metrics], key + ":metrics")
            metric_ratios = []
            for metric in metrics:
                name, prefix = metric["name"], key + ":" + metric["name"]
                node = actual.get(name, {})
                counters, ratio = ratios[(uuid, name)]
                check(prefix + ":name", node.get("name") == str(metric.get("desc") or name))
                check(prefix + ":counts", all(type(node.get(field)) is int
                      and node[field] == counters.get(field, 0) for field in (*counter_fields, "skipped")))
                score(prefix, node, ratio)
                if ratio is not None:
                    metric_ratios.append(ratio)
            ratio = sum(metric_ratios) / len(metric_ratios) if metric_ratios else None
            quality = not v3 or frozen[uuid]["score_role"] == "quality"
            if v3 and (not quality or len(metric_ratios) != len(metrics) or not source_complete):
                ratio = None
            score(key, row, ratio)
            if quality:
                if ratio is not None:
                    scored.append((ratio, weight))
                else:
                    quality_incomplete = True
        ratio = None
        if scored and (not v3 or (source_complete and not quality_incomplete)):
            total_weight = sum(weight for _, weight in scored)
            ratio = (sum(ratio * weight for ratio, weight in scored) / total_weight if total_weight > 0
                     else sum(ratio for ratio, _ in scored) / len(scored))
            category_ratios.append(ratio)
        score("dashboard.category:" + category, category_node, ratio)
    global_ratio = sum(category_ratios) / len(category_ratios) if category_ratios else None
    if v3:
        quality_categories = {category for uuid, (_, _, category, _) in definitions.items() if frozen[uuid]["score_role"] == "quality"}
        if not source_complete or any(category_rows.get(category, {}).get("score") is None for category in quality_categories):
            global_ratio = None
    score("dashboard.global", global_node, global_ratio)
    return result()


def validate_diagnosis(task, records, diagnosers, *, source_trace_ids, source_span_ids,
                       expected, expected_skips=None, expected_evidence=None):
    failures, checks = [], {}

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            failures.append(name)

    check("task_succeeded", task.get("status") == "succeeded")
    stats = task.get("stats") or {}
    count = (stats.get("source") or {}).get("total")
    check("source_count", type(count) is int and count == len(source_trace_ids))
    check("record_identity_unique", all(isinstance(r.get("uuid"), str) and r["uuid"] for r in records)
          and len({r.get("uuid") for r in records}) == len(records))
    check("record_source", all(r.get("target_id") in source_trace_ids for r in records))
    check("record_diagnoser", all(r.get("diagnoser_uuid") in {
        d["uuid"] for d in diagnosers.values()} for r in records))
    counts = Counter()
    outcomes = {}
    expected_skips = expected_skips or {}
    expected_evidence = expected_evidence or {}
    check("skip_declarations", set(expected_skips) <= set(diagnosers))
    check("skip_reason_diagnosers", set(stats.get("skip_reasons") or {})
          <= {d["uuid"] for d in diagnosers.values()})
    for key, diagnoser in diagnosers.items():
        declaration = expected_skips.get(key, {})
        skipped = dict.fromkeys(source_trace_ids, declaration) if isinstance(declaration, str) else declaration
        valid_skips = (isinstance(skipped, dict) and set(skipped) <= source_trace_ids
            and all(reason in {"not_applicable", "missing_required_evidence", "privacy_redacted", "budget_exhausted"}
                for reason in skipped.values()))
        check(key + ":skip_declarations", valid_skips)
        skipped = skipped if valid_skips else {}
        evaluated = source_trace_ids - set(skipped)
        selected = [r for r in records if r.get("diagnoser_uuid") == diagnoser["uuid"]]
        check(key + ":trace_coverage", len(selected) == len(evaluated)
              and {r.get("target_id") for r in selected} == evaluated)
        check(key + ":skipped_no_record", not any(r.get("target_id") in skipped for r in selected))
        reasons = ((stats.get("skip_reasons") or {}).get(diagnoser["uuid"]) or {})
        check(key + ":skip_reasons", isinstance(reasons, dict)
              and all(type(count) is int and count > 0 for count in reasons.values())
              and reasons == dict(Counter(skipped.values())))
        names = {metric["name"] for metric in diagnoser["spec"]["metrics"]}
        outcomes[key] = [{"trace_id": trace, "skipped": True, "skip_reason": reason}
            for trace, reason in sorted(skipped.items())]
        for record in selected:
            metrics = record.get("metrics") or []
            prefix = key + ":" + str(record.get("target_id"))
            check(prefix + ":metric_names", len(metrics) == len(names)
                  and {m.get("name") for m in metrics} == names)
            verdicts = [m.get("verdict") for m in metrics]
            derived = "bad" if "bad" in verdicts else "failed" if "failed" in verdicts else "good"
            check(prefix + ":record_verdict", record.get("verdict") == derived)
            execution = (record.get("execution") or {}).get("status")
            check(prefix + ":execution", execution in ("succeeded", "failed")
                  and (execution != "failed" or all(v == "failed" for v in verdicts)))
            for metric in metrics:
                verdict = metric.get("verdict")
                check(prefix + ":verdict", verdict in ("good", "bad", "failed"))
                counts[(diagnoser["uuid"], metric.get("name"), verdict)] += 1
                evidence = metric.get("evidence") or {}
                declared_evidence = expected_evidence.get(key, {}).get(record.get("target_id"), {})
                if "counts" in declared_evidence:
                    actual_counts = evidence.get("counts")
                    check(prefix + ":tool_counts", isinstance(actual_counts, dict)
                        and set(actual_counts) == {"success", "failure", "unknown"}
                        and all(type(v) is int and v >= 0 for v in actual_counts.values())
                        and actual_counts == declared_evidence["counts"])
                if "verification_result" in declared_evidence:
                    check(prefix + ":verification_result", (evidence.get("verification") or {}).get("result")
                        == declared_evidence["verification_result"])
                exempted = evidence.get("exempted") is True
                outcomes[key].append({"trace_id": record.get("target_id"),
                    "metric": metric.get("name"), "verdict": verdict, "exempted": exempted})
                if key in expected:
                    check(prefix + ":expected", verdict == expected[key])
                check(prefix + ":applicable", not exempted)
                if verdict in ("bad", "failed"):
                    check(prefix + ":reason", isinstance(metric.get("reason"), str)
                          and bool(metric["reason"].strip()))
                findings = evidence.get("bad_spans", [])
                check(prefix + ":evidence_source", isinstance(findings, list)
                      and all(item.get("span_id") in source_span_ids for item in findings))
                if verdict == "bad" and key in {
                    "tool_success", "stuck_loop", "high_risk_command", "sensitive_leakage",
                    "rewrite_overlap", "test_execution",
                }:
                    check(prefix + ":bad_span_evidence", bool(findings))
        for name in names:
            actual = ((stats.get("diagnosers") or {}).get(diagnoser["uuid"]) or {}).get(name) or {}
            prefix = key + ":stats:" + name
            for verdict in ("good", "bad", "failed"):
                check(prefix + ":" + verdict, type(actual.get(verdict)) is int
                      and actual[verdict] == counts[(diagnoser["uuid"], name, verdict)])
            check(prefix + ":total", type(actual.get("total")) is int
                  and actual["total"] == sum(counts[(diagnoser["uuid"], name, v)]
                    for v in ("good", "bad", "failed")))
            check(prefix + ":skipped", type(actual.get("skipped", 0)) is int
                  and actual.get("skipped", 0) == len(skipped))
    return {"verified": not failures, "checks": checks, "failures": failures, "outcomes": outcomes}


class PlatformClient:
    def __init__(self, endpoint, *, target=None, dashboard_sessions=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Diagnostic E2E requires a credential-free loopback platform")
        self.endpoint = endpoint.rstrip("/")
        self.target = target or {}
        self.dashboard_sessions = dashboard_sessions
        self.client = httpx.Client(base_url=self.endpoint, timeout=60, trust_env=False)
        self.ledger = []
        cookie = (self.target.get("dashboard_headers") or {}).get("Cookie")
        if cookie:
            for name, item in SimpleCookie(cookie).items():
                self.client.cookies.set(name, item.value)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.client.close()

    def request(self, method, path, *, body=None, params=None, allowed=(200, 201, 202), timeout=None):
        if self.dashboard_sessions is not None:
            current = self.dashboard_sessions.refresh(self.target)
            cookie = (current.get("dashboard_headers") or {}).get("Cookie", "")
            previous = (self.target.get("dashboard_headers") or {}).get("Cookie", "")
            if cookie != previous:
                self.client.cookies.clear()
                for name, item in SimpleCookie(cookie).items():
                    self.client.cookies.set(name, item.value)
            self.target = current
        headers = {"Origin": self.endpoint, "Referer": self.endpoint + "/"}
        if method not in {"GET", "HEAD"}:
            headers["X-CSRFToken"] = self.client.cookies.get("csrftoken", "")
        options = {} if timeout is None else {"timeout": timeout}
        response = self.client.request(method, path, json=body, params=params, headers=headers, **options)
        self.ledger.append({"method": method, "path": path, "status": response.status_code})
        if response.status_code not in allowed:
            error = RuntimeError(f"Platform {method} {path}: HTTP {response.status_code}")
            error.http_status = response.status_code
            raise error
        value = response.json()
        if response.status_code < 400 and value.get("code") != 0:
            raise RuntimeError(f"Platform {method} {path}: code {value.get('code')}")
        return value.get("data", value)

    @property
    def base(self):
        return "/api/v1/spaces/" + self.target["space_id"] + "/diagnose"

    def scoped(self, suffix, method="GET", *, body=None, params=None, agent=True, **kwargs):
        params = dict(params or {})
        if agent:
            params["agent_uuid"] = self.target["agent_uuid"]
        return self.request(method, self.base + suffix, body=body, params=params, **kwargs)

    def create_dataset(self, name, trace_ids):
        return self.scoped("/datasets", "POST", body={"name": name, "spec": {"query": {
            "where": trace_filter(trace_ids), "time_range": "2h", "time_offset": "0m",
            "limit": 100, "group_time_window_min": 60}}})

    def builtin_rules(self):
        rows = self.scoped("/diagnosers")
        rows = rows.get("items", []) if isinstance(rows, dict) else rows
        expected = {"tool_success", "total_latency", "stuck_loop", "high_risk_command",
                    "sensitive_leakage", "user_interruption", "rewrite_overlap", "test_execution"}
        selected = {r["spec"].get("executor"): r for r in rows
                    if r.get("type") == "builtin" and r.get("mode") == "rule"}
        if set(selected) != expected:
            raise AssertionError("Platform did not expose the eight Coding rules")
        return selected

    def create_job(self, name, dataset, diagnosers, *, save_badcase_only=False):
        return self.scoped("/jobs", "POST", body={"name": name, "trigger_type": "manual",
            "dataset_uuid": dataset["uuid"], "diagnoser_uuids": [d["uuid"] for d in diagnosers.values()],
            "spec": {} if save_badcase_only is None else {"save_badcase_only": save_badcase_only}})

    def dashboard_stats(self, job, task, diagnosers, *, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.scoped("/stats", agent=False, params={"job_uuid": job["uuid"],
                "start_time": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds"),
                "end_time": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")})
            proof = validate_stats(value, task, diagnosers)
            if proof["verified"]:
                return {**proof, "response": value}
            time.sleep(1)
        return {**proof, "response": value}

    def execute(self, job, *, timeout=240):
        triggered = self.scoped("/jobs/" + job["uuid"] + "/trigger", "POST", agent=False)
        task_uuid = triggered["task_uuid"]
        deadline = time.monotonic() + timeout
        task = {"uuid": task_uuid}
        try:
            while time.monotonic() < deadline:
                task = self.scoped("/tasks/" + task_uuid, agent=False)
                if task.get("status") in {"succeeded", "failed", "canceled"}:
                    return task
                time.sleep(1)
            raise TimeoutError("Diagnostic task did not reach terminal state")
        except BaseException as error:
            error.task = task
            raise

    def records(self, job, task, *, expected_count, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Records accept service-local naive bounds only. The explicit
            # task/job IDs select this fresh execution inside the server's
            # default local window without guessing its timezone.
            listing = self.scoped("/records", agent=False, params={"job_uuid": job["uuid"],
                "task_uuid": task["uuid"], "limit": 100})
            if listing.get("total") == expected_count and len(listing.get("items", [])) == expected_count:
                return [self.scoped("/records/" + row["uuid"], agent=False) for row in listing["items"]]
            time.sleep(1)
        raise TimeoutError("Diagnosis record visibility/count did not match execution")

    def feedback(self, record, *, remark="Independent E2E source evidence reviewed", timeout=30):
        self.scoped("/records/" + record["uuid"] + "/feedback", "POST", agent=False,
            body={"review_status": "confirmed", "feedback": {"remark": remark}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            latest = self.scoped("/records/" + record["uuid"], agent=False)
            if latest.get("review_status") == "confirmed" and (latest.get("feedback") or {}).get("remark") == remark:
                if latest["metrics"] != record["metrics"] or latest["target_id"] != record["target_id"]:
                    raise AssertionError("Feedback changed source identity or original metrics")
                return latest
            time.sleep(.25)
        raise TimeoutError("Append-style feedback not visible")
