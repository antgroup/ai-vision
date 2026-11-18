"""Current-source OceanBase platform with Linux-only build execution.

Go compilers, buildversion programs and the deployment CLI execute inside
Docker. macOS only orchestrates containers and hosts native DSH/browser tests.
"""
from contextlib import contextmanager
from copy import deepcopy
from http.cookies import CookieError, SimpleCookie
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import uuid
import shutil
import subprocess
from urllib.parse import urlsplit

import httpx


GO_IMAGE = "golang:1.25.7-bookworm"
CLI_IMAGE = "aivision-diagnostics-docker-cli:local"
_PATCH_LOCK = threading.RLock()
PRODUCTS = {"codex": "Codex", "dsh02": "DeepSeekHarness"}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _regression_runner(runner):
    # native_agent_e2e also has a run_suite module. Bind the already loaded
    # diagnostic runner while resolving regressions' explicit imports.
    with _PATCH_LOCK:
        before_path, previous = sys.path[:], sys.modules.get("run_suite")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            sys.modules["run_suite"] = runner
            return _load(Path(__file__).with_name("regressions.py"), "_diagnostics_platform_regressions")
        finally:
            sys.path[:] = before_path
            if previous is None: sys.modules.pop("run_suite", None)
            else: sys.modules["run_suite"] = previous


def framework_platform():
    directory = Path(__file__).resolve().parent.parent / "framework_e2e"
    with _PATCH_LOCK:
        before_path = sys.path[:]
        previous = {key: sys.modules.get(key) for key in ("suite", "signoz_precision")}
        try:
            sys.path.insert(0, str(directory))
            for key in previous:
                sys.modules[key] = _load(directory / (key + ".py"), key)
            return _load(directory / "docker_platform.py", "_diagnostics_shared_platform")
        finally:
            sys.path[:] = before_path
            for key, value in previous.items():
                if value is None: sys.modules.pop(key, None)
                else: sys.modules[key] = value


def native_local_docker():
    with _PATCH_LOCK:
        before_path = sys.path[:]
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "native_agent_e2e"))
            from drivers.codex import local_docker
            return local_docker()
        finally:
            sys.path[:] = before_path


def official_candidate(smoke, runtime, namespace, base_image):
    """Create a labeled alias of the pinned runtime, with no precision overlay."""
    if not re.fullmatch(r"aiv-e2e-[a-z0-9-]+", namespace) or not re.fullmatch(
            r"ghcr\.io/ob-labs/(?:signoz-ob|signoz)@sha256:[0-9a-f]{64}", base_image):
        raise ValueError("Official SigNoz alias requires a run namespace and pinned digest")
    image = "aivision-signoz-e2e:" + namespace
    listing = ["docker", "image", "ls", "-q", "--filter", "reference=" + image]
    if smoke.run("signoz-official-find", listing).stdout.strip():
        owner = smoke.run("signoz-official-owner", ["docker", "image", "inspect", "--format",
            '{{ index .Config.Labels "aivision.framework-e2e.namespace" }}', image]).stdout.strip()
        if owner != namespace: raise ValueError("SigNoz alias belongs to another run")
    inspect_base = ["docker", "image", "inspect", "--format", "{{.Id}}", base_image]
    local = smoke.run("signoz-official-base-id", inspect_base, check=False)
    if local.returncode:
        smoke.run("signoz-official-pull", ["docker", "pull", base_image], timeout=600)
        local = smoke.run("signoz-official-base-id", inspect_base)
    base_id = local.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_id):
        raise ValueError("Pinned official SigNoz image has no valid local identity")
    work = Path(runtime) / "signoz-official"
    work.mkdir()
    try:
        # BuildKit resolves registry metadata even when the pinned digest is
        # cached. The local image ID and classic builder keep this alias offline.
        (work / "Dockerfile").write_text("FROM " + base_id + "\n", encoding="utf-8")
        smoke.run("signoz-official-image", ["docker", "build", "--pull=false", "--label",
            "aivision.framework-e2e.namespace=" + namespace, "--tag", image, str(work)],
            env={**smoke.env, "DOCKER_BUILDKIT": "0"}, timeout=600)
        image_id = smoke.run("signoz-official-image-id", ["docker", "image", "inspect", "--format", "{{.Id}}", image]).stdout.strip()
        owner = smoke.run("signoz-official-owner", ["docker", "image", "inspect", "--format",
            '{{ index .Config.Labels "aivision.framework-e2e.namespace" }}', image]).stdout.strip()
        if owner != namespace or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (image_id, base_id)):
            raise ValueError("Official SigNoz alias identity or ownership is invalid")
        return {"image": image, "image_id": image_id, "official_base_image": base_image,
            "official_base_image_id": base_id, "precision_fix_applied": False,
            "acceptance_scope": "semantic_diagnostics; no strict source nanosecond parity claim",
            "build_inputs_removed": True, "candidate_published": False, "official_fixed_image_published": False}
    finally:
        shutil.rmtree(work)


def current_blueprint(original, repo, source, *args, **kwargs):
    plan = original(source, *args, **kwargs)
    agentlens = next(c for c in plan["components"] if c["name"] == "agentlens")
    for name in ("src", "obschema"):
        path = repo / "agentlens" / name
        if not path.exists():
            raise ValueError("Missing current AgentLens source: " + name)
        agentlens.setdefault("mounts", []).append(f"type=bind,source={path},target=/app/{name},readonly")
    agentlens["env"]["PYTHONDONTWRITEBYTECODE"] = "1"
    # This caller mounts the current implementation, so its database readiness
    # must remain strict even when the base images require legacy HTTP probes.
    agentlens["health"] = deepcopy(next(c["health"] for c in source["components"]
                                       if c["name"] == "agentlens"))
    frontend = repo / "ai-vision/dist"
    if not (frontend / "index.html").is_file():
        raise ValueError("Build the current frontend before UI acceptance")
    template = repo / "ai-vision/vfe/docker/nginx.conf.template"
    if not template.is_file():
        raise ValueError("Missing current frontend Nginx template")
    fe = next(c for c in plan["components"] if c["name"] == "fe")
    fe.setdefault("mounts", []).append(f"type=bind,source={frontend},target=/usr/share/nginx/html,readonly")
    fe["mounts"].append(f"type=bind,source={template},target=/etc/nginx/templates/default.conf.template,readonly")
    # The pinned baseline can predate this default in the current FE Dockerfile.
    # Its official envsubst hook must receive it when mounting the current template.
    fe.setdefault("env", {}).setdefault("AIVISION_TRUST_PROXY_HEADERS", "false")
    return plan


def build_command(command, *, repo, runtime, cwd=None, env=None, image=GO_IMAGE):
    """Keep all build inputs and outputs at the same host/container paths."""
    env = env or {}
    prefix = ["docker", "run", "--rm", "--network", "host",
        "--user", str(os.getuid()) + ":" + str(os.getgid()),
        "--mount", f"type=bind,source={repo},target={repo},readonly",
        "--mount", f"type=bind,source={runtime},target={runtime}",
        "--env", "HOME=/tmp", "--env", "GOTOOLCHAIN=local", "--env", "GOMAXPROCS=2"]
    if cwd:
        prefix += ["--workdir", str(cwd)]
    for key in ("GOCACHE", "GOMODCACHE", "GOTMPDIR", "GOOS", "GOARCH", "CGO_ENABLED", "GOFLAGS", "GOPROXY"):
        if key in env:
            value = str(env[key])
            if key in {"GOCACHE", "GOMODCACHE", "GOTMPDIR"}:
                path = Path(value)
                if not path.is_relative_to(runtime):
                    raise ValueError("Go cache must belong to this run")
                path.mkdir(parents=True, exist_ok=True)
            prefix += ["--env", key + "=" + value]
    for key in ("HTTP_PROXY", "HTTPS_PROXY"):
        if env.get(key):
            parsed = urlsplit(env[key])
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password:
                raise ValueError("Build proxy must be credential-free loopback HTTP")
            prefix += ["--env", key + "=" + env[key]]
    if "GOCACHE" not in env:
        cache = runtime / "cli-go-cache"
        cache.mkdir(exist_ok=True)
        prefix += ["--env", "GOCACHE=" + str(cache), "--env", "CGO_ENABLED=0"]
    return [*prefix, "--entrypoint", "/usr/bin/env", image, *command]


def remove_socket_container(prefix, name, env):
    """Remove this invocation's container and prove absence on the same daemon."""
    removal_error = None
    try:
        subprocess.run([*prefix, "rm", "--force", name], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        removal_error = error
    listing = subprocess.run([*prefix, "container", "ls", "--all", "--quiet",
        "--filter", "name=^/" + name + "$"], env=env, capture_output=True, text=True, timeout=10)
    if listing.returncode or listing.stdout.strip():
        raise RuntimeError("Cannot confirm owned Docker socket container cleanup: " + name) from removal_error


def daemon_socket(endpoint, *, image=CLI_IMAGE, env=None):
    """Inspect the bind source in the daemon, never the client's forwarded socket."""
    parsed = urlsplit(endpoint or "")
    if parsed.scheme != "unix" or parsed.netloc or not parsed.path.startswith("/") or parsed.query or parsed.fragment:
        raise ValueError("CLI container requires an explicit local Docker socket")
    # Linux endpoints refer directly to the daemon host (including rootless
    # sockets). Colima and OrbStack forward a VM socket to a different Mac path.
    source = Path("/var/run/docker.sock") if platform.system() == "Darwin" else Path(parsed.path)
    name = "aiv-diagnostics-socket-" + uuid.uuid4().hex
    prefix = ["docker", "--host", endpoint]
    probe = [*prefix, "run", "--rm", "--name", name, "--network=none",
        "--user", str(os.getuid()) + ":" + str(os.getgid()),
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
        "--mount", f"type=bind,source={source},target=/probe/docker.sock,readonly",
        "--entrypoint", "/bin/sh", image, "-ec",
        "test -S /probe/docker.sock && stat -c %g /probe/docker.sock"]
    try:
        result = subprocess.run(probe, env=env, capture_output=True, text=True, timeout=20)
        if result.returncode or not re.fullmatch(r"[0-9]+", result.stdout.strip()):
            raise RuntimeError("Cannot inspect daemon Docker socket ownership: " +
                (result.stderr.strip() or "missing socket or invalid group ID"))
        return source, int(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Cannot inspect daemon Docker socket ownership") from error
    finally:
        # A timed-out Docker client can leave its container running.
        remove_socket_container(prefix, name, env)


def cli_command(command, *, repo, runtime, image=CLI_IMAGE, socket_path=Path("/var/run/docker.sock"), socket_gid=None):
    if type(socket_gid) is not int or socket_gid < 0:
        raise ValueError("CLI container requires a verified daemon socket group ID")
    home = runtime / "cli-home"
    home.mkdir(exist_ok=True)
    return ["docker", "run", "--rm", "--network", "host", "--group-add", str(socket_gid),
        "--user", str(os.getuid()) + ":" + str(os.getgid()),
        "--mount", f"type=bind,source={socket_path},target=/var/run/docker.sock",
        "--mount", f"type=bind,source={repo},target={repo},readonly",
        "--mount", f"type=bind,source={runtime},target={runtime}",
        "--workdir", str(runtime), "--env", "HOME=" + str(home),
        "--env", "DOCKER_HOST=unix:///var/run/docker.sock",
        "--env", "NO_PROXY=127.0.0.1,localhost", "--entrypoint", "/usr/bin/env", image, *command]


@contextmanager
def container_builds(shared, repo, *, source_archive=None, official_signoz=False, cli_image=CLI_IMAGE, docker_endpoint=None):
    # All import overrides and function replacements have a single owner in this
    # interpreter. The original helpers and archive downloader are restored even
    # when deployment, diagnosis or cleanup raises.
    with _PATCH_LOCK:
        with _container_builds(shared, repo, source_archive=source_archive,
                               official_signoz=official_signoz, cli_image=cli_image, docker_endpoint=docker_endpoint):
            yield shared


@contextmanager
def _container_builds(shared, repo, *, source_archive, official_signoz, cli_image, docker_endpoint):
    """Adapt the established deployment helper without changing its defaults."""
    if source_archive is not None and official_signoz:
        raise ValueError("Choose official SigNoz or the verified source archive")
    original_smoke, original_blueprint = shared.Smoke, shared.deployment_blueprint
    original_candidate, original_clean = shared.build_candidate, shared.clean_environment
    candidate_globals = shared.build_candidate.__globals__
    original_download = candidate_globals.get("download_source")
    if source_archive is not None:
        if original_download is None:
            raise ValueError("Source archive override requires the verified source builder")
        # The existing builder still verifies its pinned checksum and extracts
        # with the same path/symlink protections before executing anything.
        candidate_globals["download_source"] = lambda destination: shutil.copyfile(source_archive, destination)
    active_runtime = {}

    class DockerSmoke(original_smoke):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.out.chmod(0o700)
            if docker_endpoint:
                self.env = {k: v for k, v in self.env.items() if k != "DOCKER_CONTEXT"}
                self.env["DOCKER_HOST"] = docker_endpoint

        def run(self, label, command, **kwargs):
            command = list(command)
            runtime = active_runtime.get("path")
            owned_container = None
            if label == "signoz-go-version":
                # build_candidate already created the run-owned precision directory.
                runtime = Path(kwargs.get("env", self.env)["GOCACHE"]).parent.parent
                active_runtime["path"] = runtime
            elif label == "build-cli" and runtime is None:
                output = next((v.removeprefix("OUTPUT=") for v in command if v.startswith("OUTPUT=")), None)
                if output is None or not Path(output).is_absolute():
                    raise ValueError("Docker CLI build requires an explicit run-owned output")
                runtime = Path(output).parent
                active_runtime["path"] = runtime
            if runtime is not None and (command[0] == "go" or label == "build-cli"):
                if label == "signoz-build":
                    kwargs["env"] = {**kwargs.get("env", self.env), "GOARCH": active_runtime["arch"]}
                command = build_command(command, repo=repo, runtime=runtime,
                    cwd=kwargs.get("cwd"), env=kwargs.get("env", self.env))
            elif runtime is not None and command[0] == str(runtime / "aiv"):
                if "socket" not in active_runtime:
                    endpoint = docker_endpoint or native_local_docker()[1]
                    active_runtime["socket"] = daemon_socket(endpoint, image=cli_image, env=self.env)
                socket_path, socket_gid = active_runtime["socket"]
                command = cli_command(command, repo=repo, runtime=runtime, image=cli_image,
                    socket_path=socket_path, socket_gid=socket_gid)
            if label == "signoz-candidate-image" and "--platform" in command:
                command[command.index("--platform") + 1] = "linux/" + active_runtime["arch"]
            if command[:3] == ["docker", "run", "--rm"]:
                owned_container = "aiv-diagnostics-build-" + uuid.uuid4().hex
                command[3:3] = ["--name", owned_container]
                # The platform images may require amd64 emulation. Compilers
                # and the CLI use the daemon's native architecture instead.
                kwargs["env"] = {k: v for k, v in kwargs.get("env", self.env).items()
                                 if k != "DOCKER_DEFAULT_PLATFORM"}
            primary_error = None
            result = None
            try:
                result = super().run(label, command, **kwargs)
                if label == "docker-info":
                    arch = json.loads(result.stdout).get("Architecture")
                    active_runtime["arch"] = {"aarch64": "arm64", "arm64": "arm64",
                        "x86_64": "amd64", "amd64": "amd64"}.get(arch)
                    if not active_runtime["arch"]: raise ValueError("Unsupported native Docker architecture")
                return result
            except BaseException as error:
                primary_error = error
                raise
            finally:
                if owned_container:
                    # --rm may already have removed it. Read back the exact name
                    # before removal, then verify absence; never prune globally.
                    failures = []
                    listing = ["docker", "ps", "-aq", "--filter", "name=^/" + owned_container + "$"]
                    try:
                        found = super().run(label + "-cleanup-find", listing, timeout=10, check=False)
                        if found.returncode:
                            raise RuntimeError("container cleanup listing failed (exit " + str(found.returncode) + ")")
                        if found.stdout.strip():
                            removed = super().run(label + "-cleanup-remove", ["docker", "rm", "--force", owned_container], timeout=10, check=False)
                            if removed.returncode:
                                failures.append("container cleanup removal failed (exit " + str(removed.returncode) + ")")
                        remaining = super().run(label + "-cleanup-verify", listing, timeout=10, check=False)
                        if remaining.returncode or remaining.stdout.strip():
                            failures.append("container cleanup absence was not verified")
                    except Exception as error:
                        failures.append(self.sanitize(error))
                    if failures:
                        evidence = {"verified": False, "container": owned_container, "failures": failures}
                        if primary_error is not None:
                            evidence["primary_error"] = {"type": type(primary_error).__name__, "message": self.sanitize(primary_error)}
                        elif result is not None:
                            evidence["primary_returncode"] = result.returncode
                        self.write(label + ".cleanup.json", json.dumps(evidence, indent=2))
                        prior = (self.sanitize(primary_error) + "; " if primary_error is not None else
                                 "primary exit " + str(result.returncode) + "; " if result is not None and result.returncode else "")
                        raise RuntimeError(prior + "owned container cleanup failed: " + "; ".join(failures)) from primary_error

    shared.Smoke = DockerSmoke
    shared.deployment_blueprint = lambda *args, **kwargs: current_blueprint(
        original_blueprint, repo, *args, **kwargs)
    def candidate(smoke, root, runtime, namespace, base_image):
        if official_signoz: return official_candidate(smoke, runtime, namespace, base_image)
        result = original_candidate(smoke, root, runtime, namespace, base_image)
        result["precision_fix_applied"] = True
        result["build"]["GOARCH"] = active_runtime["arch"]
        return result
    shared.build_candidate = candidate
    shared.clean_environment = lambda env: {key: value for key, value in original_clean(env).items()
                                           if key != "DOCKER_DEFAULT_PLATFORM"}
    try:
        yield shared
    finally:
        shared.Smoke, shared.deployment_blueprint = original_smoke, original_blueprint
        shared.build_candidate, shared.clean_environment = original_candidate, original_clean
        if original_download is not None:
            candidate_globals["download_source"] = original_download


def _native_suite_passed(summary, *, extended=False):
    from scenarios import scenarios
    if not isinstance(summary, dict):
        return False
    expected = {app + "-" + case.name for app in PRODUCTS
        for case in scenarios(include_extended=extended)}
    rows = summary.get("cases")
    return (summary.get("verified") is True and summary.get("stage") == "platform_diagnosis"
        and type(summary.get("expected")) is int and type(summary.get("completed")) is int
        and summary["expected"] == summary["completed"] == len(expected) == (28 if extended else 20)
        and isinstance(rows, list) and len(rows) == len(expected)
        and all(isinstance(row, dict) and isinstance(row.get("case"), str) for row in rows)
        and {row.get("case") for row in rows} == expected
        and all(row.get("verified") is True and row.get("source_verified") is True
            and row.get("diagnosis_verified") is True and not row.get("error")
            and not row.get("failures") for row in rows))


def suite_passed(summary):
    """Keep the original exact twenty-case acceptance contract."""
    return _native_suite_passed(summary)


def expanded_suite_passed(core, regressions):
    """Require all native cases, both quality matrices and four repair paths."""
    if not _native_suite_passed(core, extended=True) or not isinstance(regressions, dict):
        return False
    expected = {app + "-" + name for app in PRODUCTS for name in
        ("quality-matrix", "verified-repair", "incomplete-verification")}
    rows = regressions.get("cases")
    if not (regressions.get("verified") is True
            and type(regressions.get("expected")) is int and type(regressions.get("completed")) is int
            and regressions["expected"] == regressions["completed"] == len(expected) == 6
            and isinstance(rows, list) and len(rows) == len(expected)
            and all(isinstance(row, dict) and isinstance(row.get("case"), str) for row in rows)
            and {row["case"] for row in rows} == expected
            and all(row.get("verified") is True and not row.get("error")
                and not row.get("failures") for row in rows)):
        return False
    for row in rows:
        if row["case"].endswith("-quality-matrix"):
            if type(row.get("profiles")) is not int or row["profiles"] != 10:
                return False
        else:
            after = 100 if row["case"].endswith("-verified-repair") else 33.3
            if (type(row.get("before_score")) not in (int, float)
                    or type(row.get("after_score")) not in (int, float)
                    or row["before_score"] != 0 or row["after_score"] != after):
                return False
    return True


def regression_evidence_passed(out):
    """Read every quality profile and persistent repair proof, not just counts."""
    scores = {"score-0": 0, "score-33.3": 33.3, "score-66.7": 66.7,
        "score-100": 100, "cohort-50": 50, "cohort-75": 75, "weighted-75": 75,
        "failed-only": None, "mixed-failure": None, "joint-13-diagnosers": "independent"}
    repair_checks = {"different_tasks", "new_source", "old_task_unchanged", "old_records_unchanged",
        "old_quality_unchanged", "after_quality", "different_periods"}

    def passed(value):
        return isinstance(value, dict) and value.get("verified") is True and not value.get("failures") and not value.get("error")

    def execution_passed(value):
        proofs = value.get("proofs") if isinstance(value, dict) else None
        return (passed(value) and isinstance(proofs, list) and bool(proofs)
            and all(passed(proof) for proof in proofs) and passed(value.get("dashboard"))
            and value.get("task", {}).get("status") == "succeeded")

    def matches_score(value, score):
        return value is None if score is None else type(value) in (int, float) and value == score

    def independent_null_is_proven(report):
        task = report.get("task", {})
        stats = task.get("stats", {})
        definitions = stats.get("diagnoser_definitions", {})
        if (stats.get("schema_version") != 3 or len(definitions) != 13
                or not any(definition.get("score_role") == "quality" and any(
                    type(counters.get("failed")) is int and counters["failed"] > 0
                    for counters in stats.get("diagnosers", {}).get(uuid, {}).values())
                    for uuid, definition in definitions.items())):
            return False
        response = report.get("dashboard", {}).get("response")
        validator = _load(Path(__file__).with_name("platform_client.py"), "_diagnostics_disk_stats")
        return validator.validate_stats(response, task, report.get("diagnosers", {}))["verified"]

    try:
        for app in PRODUCTS:
            quality = out / (app + "-quality")
            summary = json.loads((quality / "summary.json").read_text())
            profiles = summary.get("profiles") if isinstance(summary, dict) else None
            if not (passed(summary) and type(summary.get("expected")) is int
                    and type(summary.get("completed")) is int and summary["expected"] == summary["completed"] == 10
                    and isinstance(profiles, list) and len(profiles) == 10
                    and all(passed(row) and isinstance(row.get("profile"), str) for row in profiles)
                    and {row["profile"] for row in profiles} == set(scores)):
                return False
            for row in profiles:
                label, score = row["profile"], row.get("score")
                expected = scores[label]
                if (expected != "independent" and score != expected
                        or score is not None and (type(score) not in (int, float) or not 0 <= score <= 100)):
                    return False
                report = json.loads((quality / (label + ".json")).read_text())
                if not (execution_passed(report) and report.get("profile") == label
                        and matches_score(report.get("actual_score"), score)
                        and matches_score(report.get("point", {}).get("global", {}).get("score"), score)
                        and report.get("job", {}).get("uuid") == row.get("job_uuid")
                        and report.get("task", {}).get("uuid") == row.get("task_uuid")):
                    return False
                if expected == "independent" and score is None and not independent_null_is_proven(report):
                    return False
            for label, score, verification in (("verified-repair", 100, True), ("incomplete-verification", 33.3, False)):
                report = json.loads((out / (app + "-" + label) / "comparison.json").read_text())
                checks = report.get("checks") if isinstance(report, dict) else None
                if not (passed(report) and isinstance(checks, dict) and set(checks) == repair_checks
                        and all(value is True for value in checks.values())
                        and report.get("with_verification") is verification):
                    return False
                before, after = report.get("before"), report.get("after")
                if not (execution_passed(before) and execution_passed(after)
                        and before["job"]["uuid"] == after["job"]["uuid"]
                        and before["job"]["dataset_uuid"] == after["job"]["dataset_uuid"] == report.get("same_dataset_uuid")
                        and before["task"]["uuid"] != after["task"]["uuid"]
                        and type(before["point"]["global"]["score"]) in (int, float)
                        and type(after["point"]["global"]["score"]) in (int, float)
                        and before["point"]["global"]["score"] == 0 and after["point"]["global"]["score"] == score
                        and before["point"]["time"] != after["point"]["time"]
                        and before.get("source_trace_ids") and after.get("source_trace_ids")
                        and not set(before["source_trace_ids"]).intersection(after["source_trace_ids"])):
                    return False
                points = after["dashboard"]["response"]["tasks"]
                for stage in (before, after):
                    selected = [point for point in points if point.get("task_uuid") == stage["task"]["uuid"]]
                    if selected != [stage["point"]]:
                        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def register_targets(*, origin, namespace, password, private, plan, smoke):
    """Public register → team/isolation Spaces → two Agents → scoped tokens."""
    from source import validate_targets
    collector = "http://127.0.0.1:" + str(plan["config"]["vars"]["GATEWAY_HTTP_PORT"])
    with httpx.Client(base_url=origin, timeout=60, trust_env=False) as client:
        def request(method, path, body=None, extra_headers=None):
            headers = {"Origin": origin, "Referer": origin + "/"}
            if method != "GET": headers["X-CSRFToken"] = client.cookies.get("csrftoken", "")
            response = client.request(method, path, json=body, headers={**headers, **(extra_headers or {})})
            private.update(cookie.value for cookie in client.cookies.jar)
            if response.status_code >= 400: raise RuntimeError(f"Public onboarding {path}: HTTP {response.status_code}")
            payload = response.json()
            if payload.get("code") != 0: raise RuntimeError("Public onboarding returned an error")
            return payload["data"]
        request("GET", "/api/v1/auth/csrf")
        request("POST", "/api/v1/auth/register", {"email": namespace + "@example.invalid",
            "name": "Native diagnostic E2E", "password": password, "password_confirm": password})
        request("GET", "/api/v1/auth/csrf")
        space = request("POST", "/api/v1/spaces", {"name": namespace + " diagnostics", "type": 2})
        space_id = space.get("space_id")
        if not space_id or space.get("type") != 2: raise ValueError("Diagnostic Judge requires a team Space")
        readback = request("GET", "/api/v1/spaces/" + space_id)
        if readback.get("space_id") != space_id or readback.get("type") != 2:
            raise ValueError("Team Space readback differs from public creation")
        isolation = request("POST", "/api/v1/spaces", {"name": namespace + " isolation", "type": 2})
        isolation_id = isolation.get("space_id")
        if not isolation_id or isolation_id == space_id or isolation.get("type") != 2:
            raise ValueError("Cross-space isolation requires a distinct team Space")
        isolated_readback = request("GET", "/api/v1/spaces/" + isolation_id)
        if isolated_readback.get("space_id") != isolation_id or isolated_readback.get("type") != 2:
            raise ValueError("Isolation Space readback differs from public creation")
        key = request("POST", f"/api/v1/spaces/{space_id}/space-keys", {"name": "native-diagnostics"})["key"]
        private.add(key)
        targets = {}
        for app, product in PRODUCTS.items():
            agent = request("POST", f"/api/v1/spaces/{space_id}/agents/create", {"name": app, "agent_product": product})
            if not agent.get("agent_uuid") or agent.get("agent_product") != product:
                raise ValueError("Unexpected native Agent onboarding identity")
            minted = request("POST", "/openapi/v1/ingest-tokens", {"agent_uuid": agent["agent_uuid"]},
                             {"Authorization": "Bearer " + key})
            if (not minted.get("token") or minted.get("token_type") != "Bearer"
                    or minted.get("space_id") != space_id or minted.get("agent_uuid") != agent["agent_uuid"]
                    or minted.get("agent_product") != product):
                raise ValueError("Ingest token readback has the wrong native scope")
            private.add(minted["token"])
            cookie = "; ".join(c.name + "=" + c.value for c in client.cookies.jar)
            private.add(cookie)
            targets[app] = {"endpoint": origin, "collector_endpoint": collector, "space_id": space_id,
                "isolation_space_id": isolation_id,
                "space_key": key, "agent_uuid": agent["agent_uuid"], "agent_product": product,
                "data_backend": "oceanbase", "collector_headers": {"Authorization": "Bearer " + minted["token"]},
                "dashboard_headers": {"Cookie": cookie}}
    return validate_targets(targets, PRODUCTS)


def _current_targets_passed(path, targets):
    """Accept only the private export belonging to the same native targets."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o777 != 0o600:
            return False
        current = json.loads(path.read_text())
        if not isinstance(current, dict) or set(current) != set(PRODUCTS):
            return False
        for app in PRODUCTS:
            target = current[app]
            if not isinstance(target, dict): return False
            for key in ("endpoint", "collector_endpoint", "space_id", "isolation_space_id",
                    "agent_uuid", "agent_product", "data_backend", "space_key"):
                if target.get(key) != targets[app].get(key): return False
            cookie = target.get("dashboard_headers", {}).get("Cookie")
            if not isinstance(cookie, str) or not cookie: return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def register_target_credentials(targets, private):
    """Register owned session values with the outer artifact sanitizer."""
    if not isinstance(targets, dict):
        return
    for target in targets.values():
        if not isinstance(target, dict):
            continue
        space_key = target.get("space_key")
        if isinstance(space_key, str) and space_key:
            private.add(space_key)
        for field, headers in target.items():
            if not isinstance(field, str) or not field.endswith("_headers") or not isinstance(headers, dict):
                continue
            for name, value in headers.items():
                if not isinstance(value, str) or not value:
                    continue
                private.add(value)
                if isinstance(name, str) and name.lower() == "cookie":
                    cookie = SimpleCookie()
                    try:
                        cookie.load(value)
                    except CookieError:
                        pass
                    private.update(morsel.value for morsel in cookie.values() if morsel.value)
                elif isinstance(name, str) and name.lower() == "authorization":
                    parts = value.split(None, 1)
                    if len(parts) == 2 and parts[1]:
                        private.add(parts[1])


def run_acceptance(*, repo, out, runtime, targets, codex_image, hook_binary, dsh_node, dsh02,
        runner=None, regression_runner=None, extended=False, after_acceptance=None, smoke=None, **_):
    path = runtime / "diagnostic-targets.json"
    current_path = runtime / "current-targets.json"
    final_path = runtime / "regression-current-targets.json"
    if current_path.exists() or current_path.is_symlink():
        raise ValueError("Current session export must be a new run-owned file")
    if extended and (final_path.exists() or final_path.is_symlink()):
        raise ValueError("Regression session export must be a new run-owned file")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream: json.dump(targets, stream)
        for app in PRODUCTS:
            target = targets.get(app, {})
            if (not isinstance(target, dict) or not isinstance(target.get("space_id"), str)
                    or not target["space_id"] or not isinstance(target.get("isolation_space_id"), str)
                    or not target["isolation_space_id"] or target["isolation_space_id"] == target["space_id"]):
                raise ValueError("Native diagnostic target requires a distinct isolation Space: " + app)
        # The native Codex container mounts out. An exported hook may live
        # elsewhere, so copy only that binary into this run's private mount.
        inputs = out / "runtime-inputs"
        inputs.mkdir(mode=0o700)
        mounted_hook = inputs / "cdxhooks"
        shutil.copyfile(hook_binary, mounted_hook)
        mounted_hook.chmod(0o700)
        if runner is None:
            with _PATCH_LOCK:
                before_path = sys.path[:]
                try: runner = _load(Path(__file__).with_name("run_suite.py"), "_diagnostics_platform_runner")
                finally: sys.path[:] = before_path
        argv = ["--repo", str(repo), "--out", str(out / "cases"), "--artifacts", str(out),
            "--targets", str(path), "--mode", "fake", "--apps", *PRODUCTS,
            "--codex-image", codex_image, "--hook-binary", str(mounted_hook),
            "--dsh-node", str(dsh_node), "--dsh02", str(dsh02)]
        argv += ["--export-current-targets", str(current_path)]
        if extended: argv += ["--extended"]
        code = runner.main(argv)
        summary = json.loads((out / "cases/summary.json").read_text())
        native_count = 28 if extended else 20
        if code != 0 or not _native_suite_passed(summary, extended=extended):
            raise AssertionError(f"Exact {native_count} native source and platform diagnosis cases did not all pass")
        for row in summary["cases"]:
            try:
                diagnosis = json.loads((out / "cases" / row["case"] / "diagnosis.json").read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise AssertionError("Missing cross-space isolation evidence: " + row["case"]) from error
            if (not isinstance(diagnosis, dict) or diagnosis.get("verified") is not True
                    or diagnosis.get("cross_space_denied") is not True):
                raise AssertionError("Cross-space isolation was not verified: " + row["case"])
        result = {"verified": True, "expected_cases": native_count, "executed_cases": native_count,
            "diagnostic_suite": {"mode": "fake", "cases": native_count,
            "stage": "platform_diagnosis", "source_verified": True, "diagnosis_verified": True,
            "cross_space_denied": True}}
        if not _current_targets_passed(current_path, targets):
            raise AssertionError("Missing private current-session export for sequential acceptance")
        if extended:
            if regression_runner is None: regression_runner = _regression_runner(runner)
            regression_out = out / "regressions"
            code = regression_runner.main(["--repo", str(repo), "--out", str(regression_out),
                "--cases", str(out / "cases"), "--targets", str(current_path), "--artifacts", str(out),
                "--export-current-targets", str(final_path),
                "--apps", *PRODUCTS, "--codex-image", codex_image, "--hook-binary", str(mounted_hook),
                "--dsh-node", str(dsh_node), "--dsh02", str(dsh02)])
            try: regressions = json.loads((regression_out / "summary.json").read_text())
            except (OSError, ValueError) as error:
                raise AssertionError("Missing expanded regression summary") from error
            if code != 0 or not expanded_suite_passed(summary, regressions) or not regression_evidence_passed(regression_out):
                raise AssertionError("Exact 34 native, quality and persistent repair results did not all pass")
            if not _current_targets_passed(final_path, targets):
                raise AssertionError("Missing private latest regression-session export")
            result["diagnostic_suite"].update(cases=34, native_cases=28, regression_cases=6,
                quality_profiles=20, regressions_verified=True)
            result.update(expected_cases=34, executed_cases=34)
        if after_acceptance is not None:
            current = json.loads((final_path if extended else current_path).read_text())
            private = smoke.private if smoke is not None else set()
            def register_private_targets(updated_targets):
                register_target_credentials(updated_targets, private)
            register_private_targets(current)
            try:
                following = after_acceptance(repo=repo, out=out, runtime=runtime, targets=current,
                    result=deepcopy(result), register_private_targets=register_private_targets)
                if not isinstance(following, dict) or following.get("verified") is not True:
                    raise AssertionError("Sequential after-acceptance stage was not verified")
                result["after_acceptance"] = following
            finally:
                # Capture in-place rotations even when the callback raises. A
                # separate replacement mapping must be explicitly registered.
                register_private_targets(current)
        return result
    finally:
        primary_error = sys.exc_info()[0] is not None
        cleanup_error = None
        private = smoke.private if smoke is not None else set()
        for exported in (current_path, final_path) if extended else (current_path,):
            try:
                if exported.exists() or exported.is_symlink():
                    if not _current_targets_passed(exported, targets):
                        raise AssertionError("Invalid private current-session export")
                    register_target_credentials(json.loads(exported.read_text()), private)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        for owned in (path, current_path, final_path) if extended else (path, current_path):
            try:
                owned.unlink(missing_ok=True)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None and not primary_error:
            raise cleanup_error


def main(argv=None, *, after_acceptance=None, runtime_base=None, ci_owned=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--codex-image", required=True)
    parser.add_argument("--hook-binary", type=Path, required=True)
    parser.add_argument("--dsh-node", type=Path, required=True)
    parser.add_argument("--dsh02", type=Path, required=True)
    parser.add_argument("--cli-image", default=CLI_IMAGE)
    parser.add_argument("--extended", action="store_true",
        help="Verify 28 native cases, 20 quality profiles and four persistent repair paths (34 results)")
    signoz = parser.add_mutually_exclusive_group()
    signoz.add_argument("--official-signoz", action="store_true", help="Default: pinned official runtime; no nanosecond overlay")
    signoz.add_argument("--signoz-source-archive", type=Path, help="Use the original SHA256-verified precision builder")
    args = parser.parse_args(argv)
    if platform.system() not in {"Linux", "Darwin"}:
        parser.error("Owned diagnostic platform requires Linux/macOS")
    if ci_owned:
        if (os.environ.get("GITHUB_ACTIONS") != "true" or not args.extended
                or after_acceptance is not None or runtime_base is not None
                or args.signoz_source_archive is not None
                or os.environ.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_PROXY")):
            parser.error("Owned CI requires fixed extended fake-only inputs without a provider")
        # Bind in the platform module, not via a caller-supplied callback:
        # validate_local.py may be running as __main__ in a separate module instance.
        from validate_local import ci_owned_fake_after_acceptance
        after_acceptance = ci_owned_fake_after_acceptance
    elif os.environ.get("GITHUB_ACTIONS"):
        parser.error("Owned diagnostic platform runs are explicitly local Linux/macOS")
    args.repo, args.out = args.repo.resolve(), args.out.resolve()
    if args.out.is_relative_to(args.repo) or args.out.exists():
        parser.error("Use a new private output directory outside the Git checkout")
    for field in ("hook_binary", "dsh_node", "dsh02", "signoz_source_archive"):
        value = getattr(args, field)
        if value is not None:
            value = value.resolve()
            if not value.is_file(): parser.error("Missing file for --" + field.replace("_", "-"))
            setattr(args, field, value)
    if not (args.repo / "ai-vision/dist/index.html").is_file():
        parser.error("Build the current frontend into ai-vision/dist first")
    with _PATCH_LOCK:
        _docker, docker_endpoint = native_local_docker()
        shared = framework_platform()
        with container_builds(shared, args.repo, source_archive=args.signoz_source_archive,
                official_signoz=args.signoz_source_archive is None, cli_image=args.cli_image, docker_endpoint=docker_endpoint):
            from docker_judge import platform_judges
            def acceptance_with_judge(**kwargs):
                with platform_judges(kwargs["namespace"] + "-agentlens"):
                    return run_acceptance(**kwargs, codex_image=args.codex_image,
                        hook_binary=args.hook_binary, dsh_node=args.dsh_node,
                        dsh02=args.dsh02, extended=args.extended,
                        after_acceptance=after_acceptance)
            return shared.main(["--repo", str(args.repo), "--out", str(args.out)],
                target_factory=register_targets, expected_cases=34 if args.extended else 20, runtime_base=runtime_base,
                acceptance=acceptance_with_judge)


if __name__ == "__main__":
    raise SystemExit(main())
