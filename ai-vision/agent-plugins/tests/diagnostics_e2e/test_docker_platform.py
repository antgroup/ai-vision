"""Build isolation is a behavioral boundary, not an optional local workaround."""
from copy import deepcopy
from contextlib import nullcontext, redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
import yaml
import docker_platform as platform

from docker_platform import build_command, cli_command, current_blueprint


def complete_summary():
    from scenarios import scenarios
    rows = [{"case": app + "-" + case.name, "source_verified": True,
        "diagnosis_verified": True, "verified": True, "failures": []}
        for app in ("codex", "dsh02") for case in scenarios()]
    return {"verified": True, "stage": "platform_diagnosis", "expected": 20,
        "completed": 20, "cases": rows}


def complete_targets():
    return {app: {"space_id": "team-one", "isolation_space_id": "isolation-one",
        "dashboard_headers": {"Cookie": "original-session"}}
        for app in platform.PRODUCTS}


def expanded_summaries():
    names = ("read-only", "tool-error-recovery", "stuck-loop", "code-change-tested",
        "code-change-untested", "rewrite-overlap", "sensitive-read", "high-risk-command",
        "privacy", "cancel", "incomplete-repair", "misreported-verification",
        "failed-verification", "repair-with-verification")
    core = {"verified": True, "stage": "platform_diagnosis", "expected": 28,
        "completed": 28, "cases": [{"case": app + "-" + name,
            "source_verified": True, "diagnosis_verified": True, "verified": True,
            "failures": []} for app in ("codex", "dsh02") for name in names]}
    rows = []
    for app in ("codex", "dsh02"):
        rows += [{"case": app + "-quality-matrix", "verified": True, "profiles": 10},
            {"case": app + "-verified-repair", "verified": True,
                "before_score": 0, "after_score": 100, "failures": []},
            {"case": app + "-incomplete-verification", "verified": True,
                "before_score": 0, "after_score": 33.3, "failures": []}]
    return core, {"verified": True, "expected": 6, "completed": 6, "cases": rows}


def write_diagnoses(out, summary):
    for row in summary["cases"]:
        path = out / row["case"] / "diagnosis.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"verified": True, "cross_space_denied": True}))


def write_regression_evidence(out):
    scores = {"score-0": 0, "score-33.3": 33.3, "score-66.7": 66.7,
        "score-100": 100, "cohort-50": 50, "cohort-75": 75, "weighted-75": 75,
        "failed-only": None, "mixed-failure": None, "joint-13-diagnosers": 87.5}
    for app in ("codex", "dsh02"):
        quality = out / (app + "-quality"); quality.mkdir(parents=True)
        profiles = []
        for label, score in scores.items():
            profiles.append({"profile": label, "verified": True, "score": score,
                "job_uuid": "job-" + label, "task_uuid": "task-" + label, "failures": []})
            report = {"verified": True, "profile": label, "actual_score": score, "failures": [],
                "task": {"uuid": "task-" + label, "status": "succeeded"},
                "job": {"uuid": "job-" + label}, "point": {"global": {"score": score}},
                "dashboard": {"verified": True}, "proofs": [{"verified": True, "failures": []}]}
            (quality / (label + ".json")).write_text(json.dumps(report))
        (quality / "summary.json").write_text(json.dumps({"verified": True,
            "expected": 10, "completed": 10, "profiles": profiles}))
        for label, after, verified in (("verified-repair", 100, True), ("incomplete-verification", 33.3, False)):
            path = out / (app + "-" + label); path.mkdir()
            comparison = {"verified": True, "failures": [], "with_verification": verified,
                "same_dataset_uuid": "dataset-one", "checks": dict.fromkeys(("different_tasks", "new_source",
                    "old_task_unchanged", "old_records_unchanged", "old_quality_unchanged", "after_quality", "different_periods"), True)}
            for side, score, trace in (("before", 0, "a" * 32), ("after", after, "b" * 32)):
                comparison[side] = {"verified": True, "failures": [],
                    "job": {"uuid": "job-one", "dataset_uuid": "dataset-one"},
                    "task": {"uuid": side, "status": "succeeded"}, "source_trace_ids": [trace],
                    "point": {"task_uuid": side, "time": side, "global": {"score": score}},
                    "dashboard": {"verified": True}, "proofs": [{"verified": True, "failures": []}]}
            comparison["after"]["dashboard"]["response"] = {"tasks": [
                comparison["before"]["point"], comparison["after"]["point"]]}
            (path / "comparison.json").write_text(json.dumps(comparison))


class _Smoke:
    def __init__(self, out, env=None, private=None):
        self.out, self.env, self.private = out, env or {}, private or set()
        self.calls, self.images = [], {}
        self.cached_base = True
        self.build_inputs = []

    def run(self, label, command, **kwargs):
        self.calls.append((label, command, kwargs))
        image = command[-1]
        output = ""
        if label == "signoz-official-base-id" and not self.cached_base:
            return subprocess.CompletedProcess(command, 1, "", "No such image")
        if label == "signoz-official-pull": self.cached_base = True
        if label == "docker-info": output = '{"Architecture":"aarch64"}'
        if label.endswith("-find") or label.endswith("-verify-removed"):
            output = "owned-id" if image.removeprefix("reference=") in self.images else ""
        if label.endswith("-owner"): output = self.images[image][0]
        if label.endswith("-image-id") or label.endswith("-base-id"): output = "sha256:" + "a" * 64
        if label.endswith("-image"):
            if label == "signoz-official-image":
                self.build_inputs.append((Path(command[-1]) / "Dockerfile").read_text())
            tag = command[command.index("--tag") + 1]
            owner = command[command.index("--label") + 1].split("=", 1)[1]
            self.images[tag] = (owner, "sha256:" + "a" * 64)
        if label.endswith("-remove"): self.images.pop(image)
        return subprocess.CompletedProcess(command, 0, output, "")


class EntrypointTests(unittest.TestCase):
    def test_ci_owned_binds_internal_fake_callback_and_rejects_other_inputs(self):
        from validate_local import ci_owned_fake_after_acceptance
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / 'repo'; bundle = repo / 'ai-vision/dist'
            bundle.mkdir(parents=True); (bundle / 'index.html').touch()
            inputs = []
            for name in ('hook-binary', 'dsh-node', 'dsh02'):
                path = root / name; path.touch(); inputs += ['--' + name, str(path)]
            argv = ['--repo', str(repo), '--out', str(root / 'owned'),
                '--codex-image', 'owned-image', *inputs, '--extended']
            shared = SimpleNamespace(main=lambda *_args, **_kwargs: 0)
            with patch.object(platform, 'framework_platform', return_value=shared), \
                    patch.object(platform, 'native_local_docker', return_value=('docker', 'unix:///owned/docker.sock')), \
                    patch.object(platform, 'container_builds', return_value=nullcontext()), \
                    patch.object(platform.platform, 'system', return_value='Linux'), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'NVIDIA_API_KEY': '',
                        'NVIDIA_PROXY': ''}, clear=True), \
                    patch.object(shared, 'main', return_value=7) as delegated:
                self.assertEqual(platform.main(argv, ci_owned=True), 7)
                self.assertEqual(delegated.call_args.kwargs['expected_cases'], 34)
                with patch.object(platform, 'run_acceptance', return_value={}) as callback:
                    delegated.call_args.kwargs['acceptance'](namespace='unit-owned')
                    self.assertIs(callback.call_args.kwargs['after_acceptance'],
                        ci_owned_fake_after_acceptance)
                for kwargs, overrides, cli in (
                    ({'after_acceptance': lambda **_: {'verified': True}}, {}, argv),
                    ({}, {'NVIDIA_API_KEY': 'public-fixture'}, argv),
                    ({}, {}, argv[:-1]),
                ):
                    with self.subTest(kwargs=kwargs, overrides=overrides, cli=cli), \
                            patch.dict(os.environ, overrides), \
                            redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                        platform.main(cli, ci_owned=True, **kwargs)
                delegated.assert_called_once()

    def test_joint_v3_null_score_requires_full_independent_counter_evidence(self):
        from test_platform import DashboardStatsTests
        entries = [("signal" + str(i), "safety", 1, None, [("fact", 1, 0, 0, 0, 100.0)]) for i in range(7)]
        entries += [("test_execution", "process", 1, 0.0, [("validation", 0, 1, 0, 0, 0.0)])]
        entries += [("quality" + str(i), "outcome", 1, 100.0, [("result", 1, 0, 0, 0, 100.0)]) for i in range(3)]
        entries += [("failed" + str(i), "outcome", 1, None, [("result", 0, 0, 1, 0, None)]) for i in range(2)]
        response, task, diags = DashboardStatsTests().fixture(entries, target_count=1,
            category_scores={"process": 0.0}, global_score=None)
        frozen = {}
        for key, diag in diags.items():
            spec, uuid = diag['spec'], diag['uuid']
            frozen[uuid] = {'uuid': uuid, 'key': key, 'name': diag['name'],
                'category': spec['category'], 'weight': spec['weight'], 'metrics': spec['metrics'],
                'score_role': 'signal' if key.startswith('signal') else 'quality',
                'version': '3.0.0', 'revision': '1.2.0', 'digest': 'a' * 64}
        task['uuid'] = 'task-joint-13-diagnosers'
        task['stats'].update(schema_version=3, score_policy_version='lightweight-v1',
            source_complete=True, skip_reasons={}, diagnoser_definitions=frozen)
        point = response['tasks'][0]
        point.update(task_uuid=task['uuid'], schema_version=3, score_policy_version='lightweight-v1',
            source_complete=True, skip_reasons={}, definition_source='task_snapshot')
        for category in point['global']['categories']:
            for row in category['diagnosers']:
                row.update({key: frozen[row['uuid']][key] for key in ('score_role', 'version', 'revision', 'digest', 'key')})
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            write_regression_evidence(out)
            report_path = out / 'codex-quality/joint-13-diagnosers.json'
            report = json.loads(report_path.read_text())
            report.update(task=task, actual_score=None, point=point, diagnosers=diags,
                dashboard={'verified': True, 'response': response})
            summary_path = report_path.parent / 'summary.json'
            summary = json.loads(summary_path.read_text())
            summary['profiles'][-1]['score'] = None
            summary_path.write_text(json.dumps(summary))
            report_path.write_text(json.dumps(report))
            self.assertTrue(platform.regression_evidence_passed(out))
            for control in ('metadata', 'count', 'definition', 'source', 'dashboard-score'):
                changed = deepcopy(report)
                if control == 'metadata': changed['task']['stats']['diagnoser_definitions']['dg-failed0']['digest'] = 'invalid'
                elif control == 'count': changed['task']['stats']['diagnosers']['dg-failed0']['result']['failed'] = 0
                elif control == 'definition': changed['task']['stats']['diagnoser_definitions'].pop('dg-failed0')
                elif control == 'source': changed['task']['stats']['source']['total'] = 2
                else: changed['dashboard']['response']['tasks'][0]['global']['score'] = 100
                report_path.write_text(json.dumps(changed))
                with self.subTest(control=control):
                    self.assertFalse(platform.regression_evidence_passed(out))

    def test_regression_evidence_rejects_false_green_profiles_and_repair_readbacks(self):
        self.assertTrue(callable(getattr(platform, "regression_evidence_passed", None)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controls = (("codex-quality/summary.json", "missing-profile"),
                ("codex-quality/summary.json", "duplicate-profile"),
                ("codex-quality/summary.json", "unknown-profile"),
                ("codex-quality/summary.json", "unverified-profile"),
                ("codex-quality/summary.json", "wrong-score"),
                ("codex-quality/score-100.json", "missing"),
                ("codex-quality/score-100.json", "unverified-proof"),
                ("codex-quality/score-0.json", "boolean-point"),
                ("codex-quality/failed-only.json", "wrong-point"),
                ("codex-quality/mixed-failure.json", "wrong-point"),
                ("codex-quality/joint-13-diagnosers.json", "empty-independent"),
                ("dsh02-verified-repair/comparison.json", "missing"),
                ("dsh02-verified-repair/comparison.json", "unverified-readback"),
                ("dsh02-verified-repair/comparison.json", "boolean-before"),
                ("dsh02-verified-repair/comparison.json", "different-job"),
                ("dsh02-incomplete-verification/comparison.json", "wrong-point"))
            good = root / "good"; write_regression_evidence(good)
            self.assertTrue(platform.regression_evidence_passed(good))
            for number, (relative, change) in enumerate(controls):
                with self.subTest(relative=relative, change=change):
                    out = root / str(number); write_regression_evidence(out)
                    path = out / relative
                    if change == "missing": path.unlink()
                    else:
                        value = json.loads(path.read_text())
                        if change == "missing-profile": value["profiles"].pop()
                        elif change == "duplicate-profile": value["profiles"][-1] = deepcopy(value["profiles"][0])
                        elif change == "unknown-profile": value["profiles"][-1]["profile"] = "unknown"
                        elif change == "unverified-profile": value["profiles"][0]["verified"] = False
                        elif change == "wrong-score": value["profiles"][0]["score"] = 100
                        elif change == "unverified-proof": value["proofs"][0]["verified"] = False
                        elif change == "boolean-point": value["actual_score"] = value["point"]["global"]["score"] = False
                        elif change == "unverified-readback": value["checks"]["old_quality_unchanged"] = 1
                        elif change == "empty-independent":
                            value["actual_score"] = value["point"]["global"]["score"] = None
                            summary_path = path.parent / "summary.json"
                            summary = json.loads(summary_path.read_text())
                            summary["profiles"][-1]["score"] = None
                            summary_path.write_text(json.dumps(summary))
                        elif change == "boolean-before":
                            value["before"]["point"]["global"]["score"] = False
                            value["after"]["dashboard"]["response"]["tasks"][0]["global"]["score"] = False
                        elif change == "different-job": value["after"]["job"]["uuid"] = "another-job"
                        elif "comparison" in relative: value["after"]["point"]["global"]["score"] = 100
                        else: value["point"]["global"]["score"] = 100
                        path.write_text(json.dumps(value))
                    self.assertFalse(platform.regression_evidence_passed(out))

    def test_expanded_summary_requires_all_28_native_cases_and_six_regression_results(self):
        self.assertTrue(callable(getattr(platform, "expanded_suite_passed", None)))
        core, regressions = expanded_summaries()
        self.assertTrue(platform.expanded_suite_passed(core, regressions))
        self.assertFalse(platform.expanded_suite_passed(complete_summary(), regressions))
        self.assertFalse(platform.suite_passed(core))
        controls = []
        for target in ("core", "regressions"):
            for key, value in (("expected", 34), ("completed", True), ("verified", 1)):
                candidate = deepcopy(core), deepcopy(regressions)
                candidate[target == "regressions"][key] = value
                controls.append(candidate)
            for change in ("missing", "duplicate", "unknown", "wrong-app", "failed", "error"):
                candidate = deepcopy(core), deepcopy(regressions)
                rows = candidate[target == "regressions"]["cases"]
                if change == "missing": rows.pop()
                elif change == "duplicate": rows[-1] = deepcopy(rows[0])
                elif change == "unknown": rows[-1]["case"] = "codex-unrequested-case"
                elif change == "wrong-app": rows[-1]["case"] = rows[-1]["case"].replace("dsh02", "dsh01")
                elif change == "failed": rows[0]["verified"] = False
                else: rows[0]["error"] = "controlled failure"
                controls.append(candidate)
        for key, value in (("stage", "native_capture"), ("source_verified", "true"),
                ("diagnosis_verified", 1), ("verified", 1), ("failures", ["controlled failure"])):
            candidate = deepcopy(core), deepcopy(regressions)
            if key == "stage": candidate[0][key] = value
            else: candidate[0]["cases"][0][key] = value
            controls.append(candidate)
        for candidate in controls:
            with self.subTest(candidate=candidate):
                self.assertFalse(platform.expanded_suite_passed(*candidate))
        for candidate in (None, [], {"cases": [None]}, {"cases": [{"case": []}]}):
            self.assertFalse(platform.expanded_suite_passed(candidate, regressions))
            self.assertFalse(platform.expanded_suite_passed(core, candidate))

    def test_expanded_summary_rejects_incomplete_profiles_and_wrong_repair_scores(self):
        self.assertTrue(callable(getattr(platform, "expanded_suite_passed", None)))
        core, regressions = expanded_summaries()
        for index, key, value in ((0, "profiles", 9), (0, "profiles", True),
                (0, "verified", "true"), (1, "before_score", 33.3),
                (1, "after_score", 66.7), (1, "before_score", False),
                (2, "after_score", 100), (2, "failures", ["old_quality_changed"])):
            with self.subTest(index=index, key=key, value=value):
                bad = deepcopy(regressions); bad["cases"][index][key] = value
                self.assertFalse(platform.expanded_suite_passed(core, bad))

    def test_full_summary_rejects_false_green_counts_missing_cases_and_capture_only(self):
        self.assertTrue(callable(getattr(platform, "suite_passed", None)))
        good = complete_summary()
        self.assertTrue(platform.suite_passed(good))
        controls = []
        for key, value in (("expected", 19), ("completed", True), ("stage", "native_capture"), ("verified", False)):
            bad = deepcopy(good); bad[key] = value; controls.append(bad)
        missing = deepcopy(good); missing["cases"].pop(); controls.append(missing)
        duplicate = deepcopy(good); duplicate["cases"][-1] = deepcopy(duplicate["cases"][0]); controls.append(duplicate)
        for key in ("source_verified", "diagnosis_verified", "verified"):
            bad = deepcopy(good); bad["cases"][0][key] = False; controls.append(bad)
        for bad in controls:
            with self.subTest(bad=bad): self.assertFalse(platform.suite_passed(bad))

    def test_callback_rotated_credentials_are_redacted_in_outer_error_and_proof(self):
        for control in ("proof-exported", "raised-exported", "raised-inplace", "proof-registered"):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory); out = runtime / "out"; out.mkdir()
                hook = runtime / "hook"; hook.write_bytes(b"hook")
                shared = platform.framework_platform()
                smoke = shared.Smoke(out, {}, {"original-session"})
                access, refresh, auth = "synthetic-rotated-access", "synthetic-rotated-refresh", "synthetic-rotated-authorization"
                later_access, later_refresh, later_auth = "synthetic-callback-access", "synthetic-callback-refresh", "synthetic-callback-authorization"
                cookie = "aivision_access=" + access + "; aivision_refresh=" + refresh
                later_cookie = "aivision_access=" + later_access + "; aivision_refresh=" + later_refresh
                def run(argv):
                    cases = Path(argv[argv.index("--out") + 1]); cases.mkdir()
                    summary = complete_summary(); write_diagnoses(cases, summary)
                    (cases / "summary.json").write_text(json.dumps(summary))
                    current = complete_targets()
                    for target in current.values():
                        target["dashboard_headers"] = {"Cookie": cookie, "Authorization": "Bearer " + auth}
                    export = Path(argv[argv.index("--export-current-targets") + 1])
                    descriptor = os.open(export, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w") as stream: json.dump(current, stream)
                    return 0
                def after(**context):
                    current = context["targets"]
                    self.assertEqual(current["codex"]["dashboard_headers"]["Cookie"], cookie)
                    self.assertNotIn(access, smoke.sanitize(access + refresh + auth))
                    observed = [cookie, access, refresh, auth]
                    if control in ("raised-inplace", "proof-registered"):
                        updated = current if control == "raised-inplace" else deepcopy(current)
                        for target in updated.values():
                            target["dashboard_headers"] = {"Cookie": later_cookie, "Authorization": "Bearer " + later_auth}
                        if control == "proof-registered": context["register_private_targets"](updated)
                        observed += [later_cookie, later_access, later_refresh, later_auth]
                    if control.startswith("raised"):
                        raise RuntimeError("callback failed: " + " | ".join(observed))
                    return {"verified": True, "debug": observed}
                kwargs = dict(repo=Path("/repo"), out=out, runtime=runtime, targets=complete_targets(),
                    codex_image="native:local", hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"),
                    runner=SimpleNamespace(main=run), after_acceptance=after, smoke=smoke)
                if control.startswith("raised"):
                    with self.assertRaisesRegex(RuntimeError, "callback failed") as caught:
                        platform.run_acceptance(**kwargs)
                    exposed = smoke.sanitize(caught.exception)
                else:
                    accepted = platform.run_acceptance(**kwargs)
                    exposed = smoke.sanitize(json.dumps(accepted))
                self.assertIn("[REDACTED]", exposed)
                for secret in (access, refresh, auth, later_access, later_refresh, later_auth):
                    self.assertNotIn(secret, exposed)
                self.assertFalse((runtime / "diagnostic-targets.json").exists())
                self.assertFalse((runtime / "current-targets.json").exists())

    def test_failed_initial_fake_registers_only_owned_handoff_and_keeps_primary_failure(self):
        shared = platform.framework_platform()
        for control in ('owned', 'foreign', 'registration-error', 'unlink-error'):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory); out = runtime / 'out'; out.mkdir()
                hook = runtime / 'hook'; hook.write_bytes(b'hook')
                private = {'original-session'}
                smoke = shared.Smoke(out, {}, private)
                rotated = 'public-rotated-' + control
                current_path = runtime / 'current-targets.json'
                def run(argv):
                    current = complete_targets()
                    for target in current.values():
                        target['dashboard_headers']['Cookie'] = 'aivision_refresh=' + rotated
                    if control == 'foreign':
                        current['codex']['space_id'] = 'foreign-space'
                    descriptor = os.open(current_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'w') as stream:
                        json.dump(current, stream)
                    cases = Path(argv[argv.index('--out') + 1]); cases.mkdir()
                    (cases / 'summary.json').write_text(json.dumps({'verified': False}))
                    return 1
                original_unlink = Path.unlink
                def unlink(path, *args, **kwargs):
                    if control == 'unlink-error' and path == current_path:
                        raise OSError('public cleanup fixture')
                    return original_unlink(path, *args, **kwargs)
                def register(targets, values):
                    if control == 'registration-error':
                        raise OSError('public registration fixture')
                    return platform_original_register(targets, values)
                platform_original_register = platform.register_target_credentials
                with patch.object(platform, 'register_target_credentials', side_effect=register), \
                        patch.object(Path, 'unlink', unlink):
                    with self.assertRaisesRegex(AssertionError, 'Exact 20 native'):
                        platform.run_acceptance(repo=Path('/repo'), out=out, runtime=runtime,
                            targets=complete_targets(), runner=SimpleNamespace(main=run), smoke=smoke,
                            codex_image='native:local', hook_binary=hook,
                            dsh_node=Path('/node'), dsh02=Path('/dsh'))
                if control in ('owned', 'unlink-error'):
                    self.assertIn(rotated, smoke.private)
                else:
                    self.assertNotIn(rotated, smoke.private)
                if control == 'unlink-error':
                    current_path.unlink()
                self.assertFalse(current_path.exists())
                self.assertFalse((runtime / 'diagnostic-targets.json').exists())

    def test_sequential_callback_requires_verified_evidence_and_latest_private_session(self):
        for control in ("passed", "failed-callback", "unverified-callback", "missing-export", "wrong-scope", "failed-core"):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory); out = runtime / "out"; out.mkdir()
                hook = runtime / "hook"; hook.write_bytes(b"hook")
                stages = []
                def run(argv):
                    stages.append("core")
                    self.assertIn("--export-current-targets", argv)
                    self.assertNotIn("--extended", argv)
                    cases = Path(argv[argv.index("--out") + 1]); cases.mkdir()
                    summary = complete_summary(); write_diagnoses(cases, summary)
                    (cases / "summary.json").write_text(json.dumps(summary))
                    if control != "missing-export":
                        current = complete_targets()
                        for target in current.values(): target["dashboard_headers"]["Cookie"] = "latest-session"
                        if control == "wrong-scope": current["codex"]["space_id"] = "other-team"
                        exported = Path(argv[argv.index("--export-current-targets") + 1])
                        descriptor = os.open(exported, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "w") as stream: json.dump(current, stream)
                    return 1 if control == "failed-core" else 0
                def after(**context):
                    stages.append("callback")
                    self.assertTrue((runtime / "diagnostic-targets.json").exists())
                    self.assertTrue((runtime / "current-targets.json").exists())
                    self.assertEqual(context["runtime"], runtime)
                    self.assertEqual(context["out"], out)
                    self.assertIs(context["result"]["verified"], True)
                    self.assertEqual(context["result"]["executed_cases"], 20)
                    self.assertTrue(all(t["dashboard_headers"]["Cookie"] == "latest-session" for t in context["targets"].values()))
                    if control == "failed-callback": raise RuntimeError("screenshot failed")
                    return {"verified": control != "unverified-callback", "screenshots": 2}
                kwargs = dict(repo=Path("/repo"), out=out, runtime=runtime, targets=complete_targets(),
                    codex_image="native:local", hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"),
                    runner=SimpleNamespace(main=run), after_acceptance=after)
                if control == "passed":
                    result = platform.run_acceptance(**kwargs)
                    self.assertEqual(result["after_acceptance"], {"verified": True, "screenshots": 2})
                else:
                    with self.assertRaises(RuntimeError if control == "failed-callback" else AssertionError):
                        platform.run_acceptance(**kwargs)
                self.assertEqual(stages, ["core", "callback"] if control in ("passed", "failed-callback", "unverified-callback") else ["core"])
                self.assertFalse((runtime / "diagnostic-targets.json").exists())
                self.assertFalse((runtime / "current-targets.json").exists())

    def test_acceptance_uses_private_targets_and_reclaims_them_on_success_or_failure(self):
        self.assertTrue(callable(getattr(platform, "run_acceptance", None)))
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            hook = runtime / "exported-hooks"; hook.write_bytes(b"linux native hook"); hook.chmod(0o755)
            for succeeds in (True, False):
                out = runtime / str(succeeds); out.mkdir()
                def run(argv):
                    target = Path(argv[argv.index("--targets") + 1])
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(json.loads(target.read_text()), complete_targets())
                    self.assertEqual(argv[argv.index("--mode") + 1], "fake")
                    self.assertNotIn("--capture-only", argv)
                    mounted_hook = Path(argv[argv.index("--hook-binary") + 1])
                    self.assertTrue(mounted_hook.is_relative_to(out))
                    self.assertEqual(mounted_hook.read_bytes(), hook.read_bytes())
                    self.assertTrue(mounted_hook.stat().st_mode & 0o100)
                    exported = Path(argv[argv.index("--export-current-targets") + 1])
                    descriptor = os.open(exported, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w") as stream: json.dump(complete_targets(), stream)
                    result = Path(argv[argv.index("--out") + 1]); result.mkdir()
                    summary = complete_summary()
                    write_diagnoses(result, summary)
                    if not succeeds: summary["cases"].pop()
                    (result / "summary.json").write_text(json.dumps(summary))
                    return 0
                call = lambda: platform.run_acceptance(repo=Path("/repo"), out=out, runtime=runtime,
                    targets=complete_targets(), runner=SimpleNamespace(main=run),
                    codex_image="native:local", hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"))
                if succeeds: self.assertTrue(call()["verified"])
                else:
                    with self.assertRaises(AssertionError): call()
                self.assertFalse((runtime / "diagnostic-targets.json").exists())

    def test_acceptance_rejects_missing_or_unproven_cross_space_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); hook = runtime / "hooks"; hook.write_bytes(b"hook")
            for number, evidence in enumerate((None, False, "true", 1, "missing-file", "invalid-json", "not-object")):
                with self.subTest(evidence=evidence):
                    out = runtime / str(number); out.mkdir()
                    def run(argv):
                        result = Path(argv[argv.index("--out") + 1]); result.mkdir()
                        summary = complete_summary(); write_diagnoses(result, summary)
                        path = result / summary["cases"][0]["case"] / "diagnosis.json"
                        record = {"verified": True}
                        if evidence is not None: record["cross_space_denied"] = evidence
                        if evidence == "missing-file": path.unlink()
                        elif evidence == "invalid-json": path.write_text("{")
                        elif evidence == "not-object": path.write_text("[]")
                        else: path.write_text(json.dumps(record))
                        (result / "summary.json").write_text(json.dumps(summary))
                        return 0
                    with self.assertRaisesRegex(AssertionError, "isolation"):
                        platform.run_acceptance(repo=Path("/repo"), out=out, runtime=runtime,
                            targets=complete_targets(), runner=SimpleNamespace(main=run), codex_image="native:local",
                            hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"))
                    self.assertFalse((runtime / "diagnostic-targets.json").exists())
            for number, invalid in enumerate((None, "team-one")):
                targets = complete_targets(); targets["codex"]["isolation_space_id"] = invalid
                runner = SimpleNamespace(main=lambda _argv: self.fail("Missing isolation reached native runner"))
                out = runtime / ("target-" + str(number)); out.mkdir()
                with self.assertRaisesRegex(ValueError, "isolation"):
                    platform.run_acceptance(repo=Path("/repo"), out=out, runtime=runtime,
                        targets=targets, runner=runner, codex_image="native:local", hook_binary=hook,
                        dsh_node=Path("/node"), dsh02=Path("/dsh"))
                self.assertFalse((runtime / "diagnostic-targets.json").exists())

    def test_official_alias_is_owned_and_foreign_alias_is_never_overwritten_or_removed(self):
        self.assertTrue(callable(getattr(platform, "official_candidate", None)))
        shared = platform.framework_platform()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); namespace = "aiv-e2e-example"
            base = "ghcr.io/ob-labs/signoz-ob@sha256:" + "b" * 64
            smoke = _Smoke(runtime)
            candidate = platform.official_candidate(smoke, runtime, namespace, base)
            self.assertFalse(candidate["precision_fix_applied"])
            self.assertEqual(candidate["official_base_image"], base)
            build = next(cmd for label, cmd, _ in smoke.calls if label.endswith("-image"))
            self.assertNotIn("--platform", build)
            shared.remove_candidate(smoke, namespace, candidate["image"])
            self.assertNotIn(candidate["image"], smoke.images)
            smoke.images[candidate["image"]] = ("another-run", "sha256:" + "a" * 64)
            previous = len(smoke.calls)
            with self.assertRaisesRegex(ValueError, "another run"):
                platform.official_candidate(smoke, runtime, namespace, base)
            with self.assertRaisesRegex(ValueError, "another run"):
                shared.remove_candidate(smoke, namespace, candidate["image"])
            self.assertTrue(candidate["image"] in smoke.images)
            self.assertFalse(any(label.endswith(("-image", "-remove")) for label, _, _ in smoke.calls[previous:]))

    def test_official_cached_digest_uses_local_id_without_manifest_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); smoke = _Smoke(runtime)
            base = "ghcr.io/ob-labs/signoz-ob@sha256:" + "b" * 64
            result = platform.official_candidate(smoke, runtime, "aiv-e2e-cached", base)
            self.assertEqual(smoke.build_inputs, ["FROM sha256:" + "a" * 64 + "\n"])
            build = next((cmd, options) for label, cmd, options in smoke.calls if label == "signoz-official-image")
            self.assertIn("--pull=false", build[0])
            self.assertEqual(build[1]["env"]["DOCKER_BUILDKIT"], "0")
            self.assertFalse(any(label == "signoz-official-pull" for label, _, _ in smoke.calls))
            self.assertEqual(result["official_base_image_id"], "sha256:" + "a" * 64)
            self.assertFalse((runtime / "signoz-official").exists())

    def test_official_missing_digest_pulls_only_the_pinned_base(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); smoke = _Smoke(runtime); smoke.cached_base = False
            base = "ghcr.io/ob-labs/signoz-ob@sha256:" + "b" * 64
            platform.official_candidate(smoke, runtime, "aiv-e2e-missing", base)
            pulls = [cmd for label, cmd, _ in smoke.calls if label == "signoz-official-pull"]
            self.assertEqual(pulls, [["docker", "pull", base]])
            self.assertFalse(any("--platform" in cmd for _, cmd, _ in smoke.calls))

    def test_context_restores_all_patches_after_failure_and_preserves_native_arch(self):
        shared = platform.framework_platform()
        originals = {key: getattr(shared, key) for key in
            ("Smoke", "deployment_blueprint", "build_candidate", "clean_environment")}
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "controlled"):
                with platform.container_builds(shared, Path("/repo"), official_signoz=True):
                    self.assertNotIn("DOCKER_DEFAULT_PLATFORM", shared.clean_environment({}))
                    self.assertIsNot(shared.build_candidate, originals["build_candidate"])
                    smoke = shared.Smoke(runtime, {}, set())
                    self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
                    raise RuntimeError("controlled")
            for key, original in originals.items(): self.assertIs(getattr(shared, key), original)

    def test_public_onboarding_uses_team_space_and_two_native_products(self):
        self.assertTrue(callable(getattr(platform, "register_targets", None)))
        calls = []
        def handle(request):
            body = json.loads(request.content) if request.content else {}
            path = request.url.path; calls.append((request.method, path, body))
            data = {}
            if path == "/api/v1/spaces" and request.method == "POST":
                self.assertEqual(body["type"], 2)
                data = {"space_id": "isolation-one" if body["name"].endswith(" isolation") else "team-one", "type": 2}
            elif path == "/api/v1/spaces/team-one": data = {"space_id": "team-one", "type": 2}
            elif path == "/api/v1/spaces/isolation-one": data = {"space_id": "isolation-one", "type": 2}
            elif path.endswith("space-keys"): data = {"key": "private-key"}
            elif path.endswith("agents/create"): data = {"agent_uuid": body["agent_product"], "agent_product": body["agent_product"]}
            elif path == "/openapi/v1/ingest-tokens":
                data = {"token": "private-ingest", "token_type": "Bearer", "space_id": "team-one",
                    "agent_uuid": body["agent_uuid"], "agent_product": body["agent_uuid"]}
            return httpx.Response(201 if path == "/api/v1/spaces" else 200,
                json={"code": 0, "data": data}, headers={"Set-Cookie": "csrftoken=private-csrf; Path=/"})
        client_class = httpx.Client
        factory = lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(handle))
        with patch.object(platform.httpx, "Client", factory):
            targets = platform.register_targets(origin="http://127.0.0.1:9999", namespace="aiv-e2e-example",
                password="private-password", private=set(), plan={"config": {"vars": {"GATEWAY_HTTP_PORT": "1234"}}}, smoke=None)
        self.assertEqual(set(targets), {"codex", "dsh02"})
        self.assertTrue(all(t["space_id"] == "team-one" and t["data_backend"] == "oceanbase" for t in targets.values()))
        self.assertEqual({t["agent_product"] for t in targets.values()}, {"Codex", "DeepSeekHarness"})
        self.assertEqual(sum(path.endswith("agents/create") for _, path, _ in calls), 2)
        self.assertTrue(all(t["isolation_space_id"] == "isolation-one" for t in targets.values()))
        self.assertTrue(any(method == "GET" and path == "/api/v1/spaces/isolation-one" for method, path, _ in calls))

    def test_public_onboarding_rejects_space_and_ingest_scope_mismatches(self):
        controls = [("space", "type", 1), ("readback", "space_id", "another-space"),
            ("readback", "type", 1), ("mint", "space_id", "another-space"),
            ("mint", "agent_uuid", "another-agent"), ("mint", "agent_product", "another-product"),
            ("mint", "token_type", "Basic"), ("mint", "token", ""),
            ("isolation", "space_id", "team-one"), ("isolation", "type", 1),
            ("isolation-readback", "space_id", "another-space"), ("isolation-readback", "type", 1)]
        client_class = httpx.Client
        for phase, key, value in controls:
            with self.subTest(phase=phase, key=key):
                def handle(request):
                    body = json.loads(request.content) if request.content else {}
                    path = request.url.path; data = {}; current = None
                    if path == "/api/v1/spaces":
                        current = "isolation" if body["name"].endswith(" isolation") else "space"
                        data = {"space_id": "isolation-one" if current == "isolation" else "team-one", "type": 2}
                    elif path == "/api/v1/spaces/team-one": current = "readback"; data = {"space_id": "team-one", "type": 2}
                    elif path == "/api/v1/spaces/isolation-one":
                        current = "isolation-readback"; data = {"space_id": "isolation-one", "type": 2}
                    elif path.endswith("space-keys"): data = {"key": "private-key"}
                    elif path.endswith("agents/create"):
                        data = {"agent_uuid": body["agent_product"], "agent_product": body["agent_product"]}
                    elif path == "/openapi/v1/ingest-tokens":
                        current = "mint"; data = {"token": "private-ingest", "token_type": "Bearer",
                            "space_id": "team-one", "agent_uuid": body["agent_uuid"], "agent_product": body["agent_uuid"]}
                    if current == phase: data[key] = value
                    return httpx.Response(200, json={"code": 0, "data": data},
                        headers={"Set-Cookie": "csrftoken=private-csrf; Path=/"})
                factory = lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(handle))
                with patch.object(platform.httpx, "Client", factory), self.assertRaises(ValueError):
                    platform.register_targets(origin="http://127.0.0.1:9999", namespace="aiv-e2e-example",
                        password="private-password", private=set(), plan={"config": {"vars": {"GATEWAY_HTTP_PORT": "1234"}}}, smoke=None)

    def test_private_targets_are_reclaimed_when_runner_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            hook = runtime / "exported-hooks"; hook.write_bytes(b"hook")
            def fail(_argv): raise RuntimeError("controlled runner failure")
            with self.assertRaisesRegex(RuntimeError, "controlled"):
                platform.run_acceptance(repo=Path("/repo"), out=runtime, runtime=runtime,
                    targets=complete_targets(), runner=SimpleNamespace(main=fail), codex_image="native:local",
                    hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"))
            self.assertFalse((runtime / "diagnostic-targets.json").exists())

    def test_extended_acceptance_uses_current_session_and_checks_all_evidence(self):
        for control in ("passed", "core-failed", "core-exception", "missing-isolation", "missing-current", "public-current",
                "malformed-current", "wrong-current-scope",
                "regression-failed", "missing-profile", "regression-exception"):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); out = root / "evidence"; out.mkdir()
                hook = root / "hook"; hook.write_bytes(b"native hook")
                core, regressions = expanded_summaries()
                targets = complete_targets()
                for target in targets.values(): target["dashboard_headers"] = {"Cookie": "original-session"}
                calls = []
                def run_core(argv):
                    calls.append("core")
                    self.assertIn("--extended", argv)
                    exported = Path(argv[argv.index("--export-current-targets") + 1])
                    result = Path(argv[argv.index("--out") + 1]); result.mkdir()
                    write_diagnoses(result, core)
                    if control == "missing-isolation":
                        (result / core["cases"][0]["case"] / "diagnosis.json").unlink()
                    (result / "summary.json").write_text(json.dumps(core))
                    if control != "missing-current":
                        current = deepcopy(targets)
                        for target in current.values(): target["dashboard_headers"]["Cookie"] = "current-session"
                        if control == "wrong-current-scope": current["codex"]["space_id"] = "other-team"
                        descriptor = os.open(exported, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "w") as stream: json.dump(current, stream)
                        if control == "public-current": exported.chmod(0o644)
                        if control == "malformed-current": exported.write_text("invalid JSON")
                    if control == "core-exception": raise RuntimeError("controlled core exception")
                    return 1 if control == "core-failed" else 0
                def run_regressions(argv):
                    calls.append("regressions")
                    exported = Path(argv[argv.index("--targets") + 1])
                    current = json.loads(exported.read_text())
                    self.assertEqual(exported.stat().st_mode & 0o777, 0o600)
                    self.assertTrue(all(t["dashboard_headers"]["Cookie"] == "current-session" for t in current.values()))
                    self.assertEqual(Path(argv[argv.index("--cases") + 1]), out / "cases")
                    self.assertEqual(Path(argv[argv.index("--artifacts") + 1]), out)
                    mounted = Path(argv[argv.index("--hook-binary") + 1])
                    self.assertEqual(mounted.read_bytes(), hook.read_bytes())
                    self.assertTrue(mounted.is_relative_to(out))
                    latest = deepcopy(current)
                    for target in latest.values(): target["dashboard_headers"]["Cookie"] = "regression-session"
                    final_path = Path(argv[argv.index("--export-current-targets") + 1])
                    descriptor = os.open(final_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w") as stream: json.dump(latest, stream)
                    if control == "regression-exception": raise RuntimeError("controlled regression exception")
                    result = Path(argv[argv.index("--out") + 1])
                    write_regression_evidence(result)
                    (result / "summary.json").write_text(json.dumps(regressions))
                    if control == "missing-profile": (result / "dsh02-quality/score-0.json").unlink()
                    return 1 if control == "regression-failed" else 0
                kwargs = dict(repo=root / "repo", out=out, runtime=root, targets=targets,
                    runner=SimpleNamespace(main=run_core), regression_runner=SimpleNamespace(main=run_regressions),
                    codex_image="native:local", hook_binary=hook, dsh_node=Path("/node"), dsh02=Path("/dsh"), extended=True)
                if control == "passed":
                    def after(**context):
                        calls.append("callback")
                        self.assertTrue(all(t["dashboard_headers"]["Cookie"] == "regression-session" for t in context["targets"].values()))
                        self.assertEqual(context["result"]["executed_cases"], 34)
                        return {"verified": True}
                    kwargs["after_acceptance"] = after
                    result = platform.run_acceptance(**kwargs)
                    self.assertIs(result["verified"], True)
                    self.assertEqual(result["expected_cases"], 34)
                    self.assertEqual(result["executed_cases"], 34)
                    self.assertEqual(result["diagnostic_suite"]["cases"], 34)
                    self.assertIs(result["diagnostic_suite"]["regressions_verified"], True)
                else:
                    with self.assertRaises(RuntimeError if control in ("core-exception", "regression-exception") else AssertionError):
                        platform.run_acceptance(**kwargs)
                expected_calls = ["core"] if control in ("core-failed", "core-exception", "missing-isolation", "missing-current",
                    "public-current", "malformed-current", "wrong-current-scope") else ["core", "regressions"]
                if control == "passed": expected_calls.append("callback")
                self.assertEqual(calls, expected_calls)
                self.assertEqual(json.loads((out / "cases/summary.json").read_text()), core)
                self.assertFalse((root / "diagnostic-targets.json").exists())
                self.assertFalse((root / "current-targets.json").exists())
                self.assertFalse((root / "regression-current-targets.json").exists())

    def test_regression_import_uses_diagnostic_runner_and_restores_native_alias(self):
        runner, native = SimpleNamespace(identity="diagnostic"), SimpleNamespace(identity="native")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "regressions.py").write_text("from run_suite import identity\nloaded_identity = identity\n")
            with patch.dict(sys.modules, {"run_suite": native}), patch.object(platform, "__file__", str(root / "docker_platform.py")):
                before = sys.path[:]
                loaded = platform._regression_runner(runner)
                self.assertEqual(loaded.loaded_identity, "diagnostic")
                self.assertIs(sys.modules["run_suite"], native)
                self.assertEqual(sys.path, before)
                (root / "regressions.py").write_text("raise RuntimeError('controlled import failure')\n")
                with self.assertRaisesRegex(RuntimeError, "controlled import"):
                    platform._regression_runner(runner)
                self.assertIs(sys.modules["run_suite"], native)
                self.assertEqual(sys.path, before)

    def test_preexisting_current_session_export_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current-targets.json"; current.write_text("caller-owned")
            with self.assertRaisesRegex(ValueError, "new run-owned"):
                platform.run_acceptance(repo=Path("/repo"), out=root, runtime=root, targets=complete_targets(),
                    codex_image="native:local", hook_binary=root / "hook", dsh_node=Path("/node"),
                    dsh02=Path("/dsh"), extended=True)
            self.assertEqual(current.read_text(), "caller-owned")
            self.assertFalse((root / "diagnostic-targets.json").exists())

    def test_patch_contexts_serialize_and_framework_imports_restore_caller_state(self):
        marker = SimpleNamespace(caller_owned=True)
        with patch.dict(sys.modules, {"suite": marker}):
            before = sys.path[:]
            shared = platform.framework_platform()
            self.assertIs(sys.modules["suite"], marker)
            self.assertEqual(sys.path, before)
        entered, finished = threading.Event(), threading.Event()
        with platform.container_builds(shared, Path("/repo"), official_signoz=True):
            def attempt():
                entered.set()
                with platform.container_builds(shared, Path("/repo"), official_signoz=True): finished.set()
            thread = threading.Thread(target=attempt); thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.wait(.05))
        thread.join(2)
        self.assertTrue(finished.is_set())

    def test_source_downloader_restores_and_precision_commands_use_daemon_arch(self):
        shared = platform.framework_platform()
        original = shared.build_candidate.__globals__["download_source"]
        candidate_globals = shared.build_candidate.__globals__
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory); archive = runtime / "source.tar.gz"; archive.write_bytes(b"source")
            with patch.object(shared, "Smoke", _Smoke), patch.object(platform.subprocess, "run"):
                with platform.container_builds(shared, Path("/repo"), source_archive=archive):
                    smoke = shared.Smoke(runtime, {}, set())
                    smoke.run("docker-info", ["docker", "info"])
                    smoke.run("signoz-go-version", ["go", "version"], env={"GOCACHE": str(runtime / "signoz-precision/cache")})
                    smoke.run("signoz-build", ["go", "build"], env={"GOARCH": "amd64"})
                    smoke.run("signoz-candidate-image", ["docker", "build", "--platform", "linux/amd64",
                        "--label", "aivision.framework-e2e.namespace=aiv-e2e-example", "--tag", "owned:example", "."])
                    compile_command = next(cmd for label, cmd, _ in smoke.calls if label == "signoz-build")
                    self.assertIn("GOARCH=arm64", compile_command)
                    image_command = next(cmd for label, cmd, _ in smoke.calls if label == "signoz-candidate-image")
                    self.assertEqual(image_command[image_command.index("--platform") + 1], "linux/arm64")
                    # The patched download still feeds the original checksum verifier.
                    destination = runtime / "copy.tar.gz"
                    candidate_globals["download_source"](destination)
                    self.assertEqual(destination.read_bytes(), archive.read_bytes())
                    with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                        candidate_globals["verify_archive"](destination, candidate_globals["SOURCE_ARCHIVE_SHA256"])
            self.assertIs(shared.build_candidate.__globals__["download_source"], original)

    def test_cli_delegates_only_local_default_twenty_with_typed_paths(self):
        self.assertTrue(callable(getattr(platform, "native_local_docker", None)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / "repo"; bundle = repo / "ai-vision/dist"
            bundle.mkdir(parents=True); (bundle / "index.html").touch()
            inputs = []
            for name in ("hook-binary", "dsh-node", "dsh02"):
                path = root / name; path.touch(); inputs += ["--" + name, str(path)]
            argv = ["--repo", str(repo), "--out", str(root / "evidence"), "--codex-image", "native:local", *inputs]
            shared = platform.framework_platform()
            with patch.object(platform, "framework_platform", return_value=shared), \
                    patch.object(platform, "native_local_docker", return_value=("docker", "unix:///owned/docker.sock")), \
                    patch.object(platform.platform, "system", return_value="Darwin"), \
                    patch.dict(os.environ, {}, clear=True), patch.object(shared, "main", return_value=7) as delegated:
                after = lambda **context: {"verified": True}
                base = root / "private-runtime-base"
                self.assertEqual(platform.main(argv, after_acceptance=after, runtime_base=base), 7)
                self.assertEqual(delegated.call_args.kwargs["runtime_base"], base)
                self.assertEqual(delegated.call_args.kwargs["expected_cases"], 20)
                self.assertIs(delegated.call_args.kwargs["target_factory"], platform.register_targets)
                self.assertTrue(callable(delegated.call_args.kwargs["acceptance"]))
                with patch.object(platform, "run_acceptance", return_value={}) as callback:
                    delegated.call_args.kwargs["acceptance"](namespace="unit-owned")
                    self.assertIs(callback.call_args.kwargs["extended"], False)
                    self.assertIs(callback.call_args.kwargs["after_acceptance"], after)
                self.assertEqual(platform.main([*argv, "--extended"]), 7)
                self.assertEqual(delegated.call_args.kwargs["expected_cases"], 34)
                with patch.object(platform, "run_acceptance", return_value={}) as callback:
                    delegated.call_args.kwargs["acceptance"](namespace="unit-owned")
                    self.assertIs(callback.call_args.kwargs["extended"], True)
            for system, github, out in (("Windows", "", root / "evidence"), ("Darwin", "true", root / "evidence"),
                    ("Darwin", "", repo / "evidence")):
                bad = argv[:]; bad[bad.index("--out") + 1] = str(out)
                with patch.object(platform.platform, "system", return_value=system), \
                        patch.dict(os.environ, {"GITHUB_ACTIONS": github}, clear=True), \
                        patch.object(platform, "native_local_docker") as inspect_docker:
                    with redirect_stderr(StringIO()), self.assertRaises(SystemExit): platform.main(bad)
                    inspect_docker.assert_not_called()

    def test_main_callback_rejects_green_suite_without_isolation_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / "repo"; out = root / "evidence"
            bundle = repo / "ai-vision/dist"; bundle.mkdir(parents=True); (bundle / "index.html").touch()
            inputs = []
            for name in ("hook-binary", "dsh-node", "dsh02"):
                path = root / name; path.touch(); inputs += ["--" + name, str(path)]
            def run(argv):
                result = Path(argv[argv.index("--out") + 1]); result.mkdir()
                summary = complete_summary(); write_diagnoses(result, summary)
                path = result / summary["cases"][0]["case"] / "diagnosis.json"
                path.write_text(json.dumps({"verified": True}))
                (result / "summary.json").write_text(json.dumps(summary)); return 0
            def deployed(_argv, *, acceptance, **_kwargs):
                out.mkdir()
                return acceptance(repo=repo, out=out, runtime=root, namespace="unit-owned", targets=complete_targets(),
                    runner=SimpleNamespace(main=run))
            shared = platform.framework_platform()
            with patch.object(platform, "framework_platform", return_value=shared), \
                    patch.object(platform, "native_local_docker", return_value=("docker", "unix:///owned/docker.sock")), \
                    patch.object(platform.platform, "system", return_value="Darwin"), \
                    patch.dict(os.environ, {}, clear=True), patch.object(shared, "main", side_effect=deployed):
                with self.assertRaisesRegex(AssertionError, "isolation"):
                    platform.main(["--repo", str(repo), "--out", str(out), "--codex-image", "native:local", *inputs])
            self.assertFalse((root / "diagnostic-targets.json").exists())


class ContainerBuildTests(unittest.TestCase):
    def test_current_agentlens_restores_database_readiness_after_legacy_baseline(self):
        root = Path(__file__).resolve().parents[4]
        shared = platform.framework_platform()
        source = yaml.safe_load((root / "deploy/blueprints/aivision.yaml").read_text(encoding="utf-8"))
        images = {key: repos[0] + "@sha256:" + "a" * 64 for key, repos in shared.IMAGE_REPOS.items()}
        baseline = shared.deployment_blueprint(source, root, root / "runtime-fixture", "aiv-e2e-test",
            images, {}, {}, "2026-10-04T00:00:00Z", legacy_source_baseline=True)
        baseline_health = next(c["health"] for c in baseline["components"] if c["name"] == "agentlens")
        self.assertEqual(baseline_health["http"], "http://127.0.0.1:${AGENTLENS_PORT}/internal/healthz")
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for name in ("src", "obschema"):
                (repo / "agentlens" / name).mkdir(parents=True)
            bundle = repo / "ai-vision/dist"
            bundle.mkdir(parents=True)
            (bundle / "index.html").write_text("<html></html>")
            template = repo / "ai-vision/vfe/docker/nginx.conf.template"
            template.parent.mkdir(parents=True)
            template.write_text("server { listen ${FE_PORT}; }\n")
            plan = current_blueprint(lambda *_args, **_kwargs: deepcopy(baseline), repo, source)
        actual = next(c["health"] for c in plan["components"] if c["name"] == "agentlens")
        expected = next(c["health"] for c in source["components"] if c["name"] == "agentlens")
        self.assertEqual(actual, expected)
        self.assertEqual(actual["http"], "http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz")
        actual["retries"] = 0
        self.assertEqual(expected["retries"], 60, "The caller must not mutate the canonical blueprint")

    def test_build_cleanup_reports_primary_and_exact_container_removal_failure(self):
        for primary, cleanup in ((False, "removed"), (True, "removed"), (True, "remove-failed"),
                (False, "remove-failed"), (True, "listing-failed"), (True, "still-present"), (True, "timeout")):
            with self.subTest(primary=primary, cleanup=cleanup), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = []; live = set()
                class ControlledSmoke(_Smoke):
                    def sanitize(self, value): return str(value).replace("private-secret", "[REDACTED]")
                    def write(self, name, value): (self.out / name).write_text(self.sanitize(value))
                    def run(self, label, command, **kwargs):
                        events.append((label, list(command)))
                        if label == "build-cli":
                            live.add(command[command.index("--name") + 1])
                            if primary: raise RuntimeError("primary build failed private-secret")
                            return subprocess.CompletedProcess(command, 0, "", "")
                        if command[:3] == ["docker", "ps", "-aq"]:
                            if cleanup == "listing-failed": return subprocess.CompletedProcess(command, 1, "", "listing failed")
                            return subprocess.CompletedProcess(command, 0, "\n".join(live), "")
                        if command[:3] == ["docker", "rm", "--force"]:
                            if cleanup == "timeout": raise subprocess.TimeoutExpired(command, 10)
                            if cleanup == "remove-failed": return subprocess.CompletedProcess(command, 1, "", "private-secret remove failed")
                            if cleanup != "still-present": live.remove(command[-1])
                            return subprocess.CompletedProcess(command, 0, "", "")
                        raise AssertionError("Unexpected command: " + str(command))
                shared = platform.framework_platform()
                with patch.object(shared, "Smoke", ControlledSmoke), platform.container_builds(shared, Path("/repo"), official_signoz=True):
                    smoke = shared.Smoke(root, {}, {"private-secret"})
                    def invoke(): return smoke.run("build-cli", ["make", "build", "OUTPUT=" + str(root / "aiv")], cwd=Path("/repo/aivision-cli"))
                    cleanup_patch = patch.object(platform.subprocess, "run", side_effect=lambda command, **kwargs: ControlledSmoke.run(smoke, "legacy-cleanup", command))
                    cleanup_patch.start()
                    self.addCleanup(cleanup_patch.stop)
                    if primary or cleanup != "removed":
                        with self.assertRaises(RuntimeError) as caught: invoke()
                        if primary: self.assertIn("primary build failed", str(caught.exception))
                        if cleanup != "removed":
                            self.assertIn("cleanup", str(caught.exception))
                            self.assertNotIn("private-secret", str(caught.exception))
                            evidence = json.loads((root / "build-cli.cleanup.json").read_text())
                            self.assertFalse(evidence["verified"])
                    else:
                        self.assertEqual(invoke().returncode, 0)
                        self.assertEqual(live, set())
                    name = events[0][1][events[0][1].index("--name") + 1]
                    self.assertTrue(name.startswith("aiv-diagnostics-build-"))
                    for _, command in events[1:]:
                        self.assertTrue(command[-1] in (name, "name=^/" + name + "$"))

    def test_current_bundle_uses_vite_output_and_components_without_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for name in ("src", "obschema"):
                (repo / "agentlens" / name).mkdir(parents=True)
            bundle = repo / "ai-vision/dist"
            bundle.mkdir(parents=True)
            (bundle / "index.html").write_text("<html></html>")
            template = repo / "ai-vision/vfe/docker/nginx.conf.template"
            template.parent.mkdir(parents=True)
            template.write_text("server { listen ${FE_PORT}; }\n")
            source = {"components": [{"name": "agentlens", "env": {}, "health": {
                "http": "http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz"}}, {"name": "fe"}]}
            plan = current_blueprint(lambda *_args, **_kwargs: source, repo, source)
            self.assertEqual(plan["components"][1]["mounts"], [
                f"type=bind,source={bundle},target=/usr/share/nginx/html,readonly",
                f"type=bind,source={template},target=/etc/nginx/templates/default.conf.template,readonly"])
            self.assertEqual(template.read_text(), "server { listen ${FE_PORT}; }\n")
            (bundle / "index.html").unlink()
            with self.assertRaisesRegex(ValueError, "frontend"):
                current_blueprint(lambda *_args, **_kwargs: source, repo, source)

    def test_current_bundle_rejects_missing_or_non_file_nginx_template(self):
        for invalid in ("missing", "directory"):
            with self.subTest(template=invalid), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                for name in ("src", "obschema"):
                    (repo / "agentlens" / name).mkdir(parents=True)
                bundle = repo / "ai-vision/dist"
                bundle.mkdir(parents=True)
                (bundle / "index.html").write_text("<html></html>")
                template = repo / "ai-vision/vfe/docker/nginx.conf.template"
                template.parent.mkdir(parents=True)
                if invalid == "directory":
                    template.mkdir()
                source = {"components": [{"name": "agentlens", "env": {}, "health": {
                    "http": "http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz"}}, {"name": "fe"}]}
                with self.assertRaisesRegex(ValueError, "Nginx template"):
                    current_blueprint(lambda *_args, _source=source, **_kwargs: _source, repo, source)

    def test_current_template_receives_default_proxy_trust_with_legacy_image(self):
        for configured, expected in ((None, "false"), ("false", "false"), ("true", "true")):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                for name in ("src", "obschema"):
                    (repo / "agentlens" / name).mkdir(parents=True)
                bundle = repo / "ai-vision/dist"
                bundle.mkdir(parents=True)
                (bundle / "index.html").write_text("<html></html>")
                template = repo / "ai-vision/vfe/docker/nginx.conf.template"
                template.parent.mkdir(parents=True)
                template.write_text('map "${AIVISION_TRUST_PROXY_HEADERS}:$http_x_forwarded_proto" $proto {}\n')
                frontend = {"name": "fe", "env": {"FE_PORT": "5174", "BE_UPSTREAM": "http://backend:8001"}}
                if configured is not None:
                    frontend["env"]["AIVISION_TRUST_PROXY_HEADERS"] = configured
                source = {"components": [{"name": "agentlens", "env": {}, "health": {
                    "http": "http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz"}}, frontend]}
                plan = current_blueprint(lambda *_args, **_kwargs: source, repo, source)
                env = next(c for c in plan["components"] if c["name"] == "fe")["env"]
                self.assertEqual(env.get("AIVISION_TRUST_PROXY_HEADERS"), expected,
                                 "the current template requires its current image's safe default")
                self.assertEqual(env["BE_UPSTREAM"], "http://backend:8001")
                self.assertEqual(env["FE_PORT"], "5174")

    def test_compiler_has_owned_paths_and_no_host_go_or_broad_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            command = build_command(["go", "build", "./cmd/community"], repo=Path("/repo"),
                runtime=runtime, cwd=runtime / "source", env={"GOCACHE": str(runtime / "cache"),
                    "NVIDIA_API_KEY": "private", "GOARCH": "amd64", "GOOS": "linux"})
            self.assertEqual(command[:3], ["docker", "run", "--rm"])
            self.assertEqual(command[-4:], ["golang:1.25.7-bookworm", "go", "build", "./cmd/community"])
            self.assertIn("GOARCH=amd64", command)
            self.assertNotIn("NVIDIA_API_KEY=private", command)
            self.assertTrue((runtime / "cache").is_dir())
            with self.assertRaises(ValueError):
                build_command(["go", "build"], repo=Path("/repo"), runtime=runtime,
                    env={"GOCACHE": "/somebody-elses-cache"})

    def test_cli_adapter_probes_daemon_once_and_keeps_client_endpoint_out_of_bind_mount(self):
        shared = platform.framework_platform()
        endpoint = "unix:///Users/fixture/.colima/owned/docker.sock"
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with patch.object(shared, "Smoke", _Smoke), patch.object(platform.subprocess, "run"), \
                    patch.object(platform, "daemon_socket", return_value=(Path("/var/run/docker.sock"), 991)) as probe:
                with platform.container_builds(shared, Path("/repo"), docker_endpoint=endpoint):
                    smoke = shared.Smoke(runtime, {"DOCKER_CONTEXT": "other-context"})
                    smoke.run("build-cli", ["make", "OUTPUT=" + str(runtime / "aiv")])
                    for _ in range(2): smoke.run("cli-version", [str(runtime / "aiv"), "version"])
                    probe.assert_called_once_with(endpoint, image=platform.CLI_IMAGE, env=smoke.env)
                    self.assertNotIn("DOCKER_CONTEXT", smoke.env)
                    self.assertEqual(smoke.env["DOCKER_HOST"], endpoint)
                    cli_calls = [call for call in smoke.calls if call[0] == "cli-version"]
                    self.assertEqual(len(cli_calls), 2)
                    for _, command, _ in cli_calls:
                        self.assertIn("type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock", command)
                        self.assertEqual(command[command.index("--group-add") + 1], "991")
                        self.assertNotIn(endpoint.removeprefix("unix://"), " ".join(command))
                        name = command[command.index("--name") + 1]
                        cleanup = [call for call in smoke.calls if call[0] == "cli-version-cleanup-verify"
                                   and call[1][-1] == "name=^/" + name + "$"]
                        self.assertEqual(len(cleanup), 1)

    def test_deployment_cli_runs_in_container_with_private_home_and_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            original = [str(runtime / "aiv"), "cast", "--file", str(runtime / "blueprint.yaml")]
            command = cli_command(original, repo=Path("/repo"), runtime=runtime, socket_gid=991)
            self.assertEqual(command[:3], ["docker", "run", "--rm"])
            self.assertEqual(command[-len(original):], original)
            self.assertIn("HOME=" + str(runtime / "cli-home"), command)
            self.assertIn("type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock", command)

    def test_deployment_cli_uses_verified_daemon_group_without_host_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            socket = runtime / "docker.sock"
            with patch.object(Path, "stat", side_effect=AssertionError("host stat is not daemon ownership")):
                command = cli_command(["aiv", "cast"], repo=Path("/repo"),
                    runtime=runtime, socket_path=socket, socket_gid=991)
            self.assertEqual(command[command.index("--group-add") + 1], "991")



if __name__ == "__main__":
    unittest.main()
