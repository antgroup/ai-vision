#!/usr/bin/env python3
"""Produce local Linux amd64 native candidates, without publishing or deployment.

Only the final allowlisted output is uploadable. Work contains compiler output,
pristine base saves, source-bearing CLI staging and raw logs; keep it private.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from native_cli_archive import ATTRIBUTIONS, CLI_MODULE, cli_archive_members, require_cli_native_payload

PLATFORM = "linux/amd64"
IMAGE_PATTERN = re.compile(r"python:3\.11\.14-slim-bookworm@sha256:[0-9a-f]{64}")
CLI_PROFILES = ["source", "native_distribution"]
# Individually reviewed public integration examples. New paths require review.
PUBLIC_EXAMPLES = (
    "langfuse/examples/anthropic.yaml", "langfuse/examples/autogen.yaml", "langfuse/examples/claude-agent-sdk.yaml",
    "langfuse/examples/crewai.yaml", "langfuse/examples/eino.yaml", "langfuse/examples/google-adk.yaml",
    "langfuse/examples/google-gemini.yaml", "langfuse/examples/instructor.yaml", "langfuse/examples/langchain-deepagents.yaml",
    "langfuse/examples/langchain-js.yaml", "langfuse/examples/langchain.yaml", "langfuse/examples/langgraph-js.yaml",
    "langfuse/examples/langgraph.yaml", "langfuse/examples/manual-js.yaml", "langfuse/examples/microsoft-agent-framework.yaml",
    "langfuse/examples/openai-agents.yaml", "langfuse/examples/openai-js.yaml", "langfuse/examples/openai.yaml",
    "langfuse/examples/pydantic-ai.yaml", "langfuse/examples/semantic-kernel.yaml",
    "nvidia-nemo-relay/examples/deep-agents.yaml", "nvidia-nemo-relay/examples/langchain-agent-middleware.yaml",
    "nvidia-nemo-relay/examples/langgraph.yaml", "nvidia-nemo-relay/examples/nodejs.yaml", "nvidia-nemo-relay/relay_tracing.py",
)
OUTPUT_FILES = frozenset(("plan.json", "candidate-receipt.json", "native-build-audit.json",
    "backend-image.tar", "agentlens-image.tar", "backend-image-audit.json", "agentlens-image-audit.json",
    "backend-context-receipt.json", "agentlens-context-receipt.json", "aiv-source-linux-x64.tar.gz",
    "aiv-native-linux-x64.tar.gz", "cli-source-audit.json", "cli-native-audit.json"))
QUALIFICATION = "build/content/import/OCI checks only; deployment, full five-service delivery and release unqualified"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key: " + key)
            value[key] = item
        return value
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True, stderr=subprocess.PIPE).strip()


def checkout(source):
    source = Path(source).resolve()
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("source root must be the actual Git checkout root")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("candidate requires a clean checkout")
    sha = git(source, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("checkout requires a full source SHA")
    remote = git(source, "remote", "get-url", "origin")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", remote)
    if not match:
        raise ValueError("origin must identify a GitHub repository")
    version = (source / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", version):
        raise ValueError("invalid root product VERSION")
    return sha, match[1], version


def run_identity(record, workflow, sha, repository, *, success):
    if not isinstance(record, dict):
        raise ValueError("workflow run must be an object")
    if any(type(record.get(key)) is not int or record[key] <= 0 for key in ("id", "run_attempt")):
        raise ValueError("workflow run ID and attempt must be positive integers")
    if (record.get("path") != ".github/workflows/" + workflow or record.get("head_sha") != sha
            or record.get("repository", {}).get("full_name") != repository
            or record.get("head_repository", {}).get("full_name") != repository):
        raise ValueError("workflow source/repository/path identity differs from checkout")
    if success and (record.get("status"), record.get("conclusion")) != ("completed", "success"):
        raise ValueError("source CI must have completed successfully")
    if not success and (record.get("status") not in {"queued", "in_progress", "completed"}
                        or record["status"] == "completed" and record.get("conclusion") != "success"):
        raise ValueError("invalid producer workflow status")
    if success and record.get("event") not in {"push", "workflow_dispatch"}:
        raise ValueError("source CI must test the actual push/manual SHA, not a synthetic merge")
    result = {key: record[key] for key in ("id", "run_attempt", "path", "head_sha", "status", "conclusion", "event")}
    result["repository"] = {"full_name": repository}
    result["head_repository"] = {"full_name": repository}
    return result


def tracked_inputs(source):
    names = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"]).decode().split("\0")
    result = {}
    for name in names:
        if not name:
            continue
        path = Path(source) / name
        # A symlink's target and bytes are both frozen by git; never read outside source.
        result[name] = (hashlib.sha256(os.readlink(path).encode()).hexdigest() if path.is_symlink() else file_hash(path))
    return result


def source_gate(pages, ci):
    if not isinstance(pages, list) or any(not isinstance(page, dict) or not isinstance(page.get("jobs"), list) for page in pages):
        raise ValueError("source CI jobs must contain paginated attempt job objects")
    gates = [job for page in pages for job in page["jobs"] if isinstance(job, dict) and job.get("name") == "ci-gate"]
    if len(gates) != 1:
        raise ValueError("source CI attempt must contain exactly one real ci-gate")
    job = gates[0]
    if (type(job.get("id")) is not int or job["id"] <= 0 or type(job.get("run_id")) is not int
            or job["run_id"] != ci["id"] or job.get("head_sha") != ci["head_sha"]
            or (job.get("status"), job.get("conclusion")) != ("completed", "success")
            or "run_attempt" in job and (type(job["run_attempt"]) is not int or job["run_attempt"] != ci["run_attempt"])):
        raise ValueError("source CI gate did not pass for the selected source/run/attempt")
    result = {key: job[key] for key in ("id", "run_id", "name", "head_sha", "status", "conclusion")}
    result["run_attempt"] = ci["run_attempt"]
    return result


def make_plan(source, source_ci, producer, python_image, source_ci_jobs):
    sha, repository, version = checkout(source)
    if not isinstance(python_image, str) or not IMAGE_PATTERN.fullmatch(python_image):
        raise ValueError("python image must be official digest-pinned python:3.11.14-slim-bookworm")
    ci = run_identity(source_ci, "ci.yml", sha, repository, success=True)
    build = run_identity(producer, "native-candidate.yml", sha, repository, success=False)
    if ci["id"] == build["id"]:
        raise ValueError("source CI and producer must be separate workflows")
    return dict(schema_version=1, source_sha=sha, repository=repository, product_version=version,
                source_ci=ci, source_ci_gate=source_gate(source_ci_jobs, ci), producer=build, python_image=python_image, platform=PLATFORM,
                cli_profiles=CLI_PROFILES.copy(), source_inputs=tracked_inputs(source))


def validate_plan(source, plan):
    expected = make_plan(source, plan["source_ci"], plan["producer"], plan["python_image"], [{"jobs": [plan["source_ci_gate"]]}])
    if expected != plan:
        raise ValueError("candidate plan changed or differs from the frozen source")
    environment = {"GITHUB_RUN_ID": str(plan["producer"]["id"]), "GITHUB_RUN_ATTEMPT": str(plan["producer"]["run_attempt"]),
                   "GITHUB_REPOSITORY": plan["repository"], "GITHUB_SHA": plan["source_sha"]}
    for key, value in environment.items():
        if key in os.environ and os.environ[key] != value:
            raise ValueError("producer environment differs from plan: " + key)
    return plan


def overlap(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def validate_build_paths(source, plugin_dist, output, work):
    paths = [Path(path) for path in (source, plugin_dist, output, work)]
    if any(path.is_symlink() for path in paths):
        raise ValueError("candidate paths must not be symlinks")
    source, plugin_dist, output, work = (path.resolve() for path in paths)
    for index, first in enumerate((source, output, work, plugin_dist)):
        for second in (source, output, work, plugin_dist)[index + 1:]:
            if overlap(first, second):
                raise ValueError("source, output, plugin and private work paths must not overlap")
    if not plugin_dist.is_file():
        raise ValueError("plugin distribution must be a regular archive")
    for path in (output, work):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError("output and private work must be absent or empty")
    return source, plugin_dist, output, work


def public_examples(source):
    provider = Path(source) / "ai-vision/agent-plugins"
    actual = {path.relative_to(provider).as_posix() for directory in ("langfuse/examples", "nvidia-nemo-relay/examples")
              for path in (provider / directory).glob("*.yaml")}
    actual.add("nvidia-nemo-relay/relay_tracing.py")
    if actual != set(PUBLIC_EXAMPLES):
        raise ValueError("public example set changed; explicit source review required")
    result = {}
    for name in PUBLIC_EXAMPLES:
        path = provider / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("public example must be a regular file: " + name)
        result[name] = dict(sha256=file_hash(path), reason="Reviewed public integration configuration/tracing example, not product implementation")
    return result


def plugin_inputs(source):
    result = {name: digest for name, digest in tracked_inputs(source).items()
              if name.startswith("ai-vision/agent-plugins/")
              and not set(Path(name).parts) & {"dist", "target", "node_modules", "build", "__pycache__"}}
    if not result:
        raise ValueError("plugin source proof is empty")
    return result


def validate_output(directory):
    entries = list(Path(directory).iterdir())
    if {path.name for path in entries} != OUTPUT_FILES or any(path.is_symlink() or not path.is_file() or not path.stat().st_size for path in entries):
        raise ValueError("candidate upload directory must contain only the exact final artifact allowlist")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_component_info(source, component, plan):
    helper = load_module(source / "scripts/build/build_info.py", "candidate_build_info")
    return helper.make_info(source, component, channel="rc", source_sha=plan["source_sha"],
                            product_version=plan["product_version"],
                            build_id=f'{plan["producer"]["id"]}.{plan["producer"]["run_attempt"]}')


class Runner:
    def __init__(self, work):
        self.work = work
        self.counter = 0
        (work / "logs").mkdir()
        (work / "home").mkdir()

    def __call__(self, argv, *, cwd=None, env=None):
        self.counter += 1
        # Do not forward credentials, registry configuration or repository tokens.
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
        environment.update(HOME=str(self.work / "home"), PYTHONDONTWRITEBYTECODE="1")
        environment.update(env or {})
        with (self.work / "logs" / f"{self.counter:03}.log").open("wb") as log:
            subprocess.run([str(item) for item in argv], cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        if list(argv)[:2] == ["go", "version"] or list(argv)[:3] == ["docker", "image", "inspect"]:
            return (self.work / "logs" / f"{self.counter:03}.log").read_text(errors="replace")
        return ""


def go_environment(work):
    return dict(GOOS="linux", GOARCH="amd64", CGO_ENABLED="0", GOFLAGS="", GOWORK="off", GOTOOLCHAIN="local",
                GOCACHE=str(work / "go-cache"), GOMODCACHE=str(work / "go-mod-cache"))


def cli_metadata(info, plan, profile):
    if (info.get("Path") != CLI_MODULE or info.get("Main", {}).get("Path") != CLI_MODULE
            or not re.fullmatch(r"go1\.25(?:\.\d+)?", info.get("GoVersion", ""))):
        raise ValueError("CLI must be the actual module built with Go 1.25")
    rows = info.get("Settings", [])
    settings = {row["Key"]: row["Value"] for row in rows}
    expected = {"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0", "vcs.revision": plan["source_sha"], "vcs.modified": "false"}
    if len(settings) != len(rows) or any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("CLI platform or VCS identity differs from frozen candidate")
    # Exact flags, rather than a string search for a plausible version.
    if settings.get("-ldflags") != f'-X {CLI_MODULE}/internal/cli.Version={plan["product_version"]}':
        raise ValueError("CLI linker version differs from root product version")
    if settings.get("-tags", "") != ("native_distribution" if profile == "native" else ""):
        raise ValueError("CLI source/native profile mismatch")
    return info


def build_clis(source, plan, private, accepted, runner):
    environment = go_environment(private)
    for profile in ("source", "native"):
        binary = private / f"aiv-{profile}"
        argv = ["go", "build", "-mod=vendor", "-buildvcs=true"]
        if profile == "native":
            argv += ["-tags", "native_distribution"]
        argv += ["-ldflags", f'-X {CLI_MODULE}/internal/cli.Version={plan["product_version"]}', "-o", binary, "."]
        runner(argv, cwd=source / "aivision-cli", env=environment)
        info = cli_metadata(json.loads(runner(["go", "version", "-m", "-json", binary])), plan, profile)
        if profile == "native":
            require_cli_native_payload(binary.read_bytes(), root=source)
        archive_path = accepted / f"aiv-{profile}-linux-x64.tar.gz"
        with tarfile.open(archive_path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
            for name in ("aiv", *ATTRIBUTIONS):
                path = binary if name == "aiv" else source / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError("CLI archive input must be a regular file: " + name)
                data = path.read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name == "aiv" else 0o644
                archive.addfile(member, io.BytesIO(data))
        members = cli_archive_members(archive_path)
        for name in ATTRIBUTIONS:
            if members[name][1] != (source / name).read_bytes():
                raise ValueError("CLI attribution bytes changed")
        if members["aiv"][1] != binary.read_bytes():
            raise ValueError("CLI archived executable changed")
        write_json(accepted / f"cli-{profile}-audit.json", dict(schema_version=1, profile=profile,
            archive_sha256=file_hash(archive_path), executable_sha256=file_hash(binary), build_info=info,
            owned_python_boundary="known embedded programs excluded" if profile == "native" else "source helpers intentionally retained",
            qualification="archive framing, exact attribution and executable metadata; no CLI lifecycle acceptance"))


def native_audit(stage, source, plan):
    receipt = read_json(stage / "receipt.json")
    if (receipt.get("schema_version") != 1 or receipt.get("target") != PLATFORM
            or receipt.get("source_revision") != plan["source_sha"] or receipt.get("inputs_stable") is not True
            or set(receipt.get("components", {})) != {"backend", "agentlens", "onboarding"}):
        raise ValueError("native producer receipt is missing or has a different source/target")
    inputs = receipt.get("input_sha256")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("native producer input proof is missing")
    for name, digest in inputs.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name not in plan["source_inputs"] or plan["source_inputs"][name] != digest:
            raise ValueError("native producer input proof differs from frozen source")
    # Deliberately project, not copy, the producer receipt: tools and dependency
    # records contain absolute compiler paths and stay in private work.
    components = {}
    for component, result in receipt["components"].items():
        if not isinstance(result.get("smoke"), list) or not result["smoke"] or not all(isinstance(item, str) for item in result["smoke"]):
            raise ValueError("native producer smoke evidence missing")
        components[component] = {key: result[key] for key in ("smoke", "sha256", "manifest_sha256", "source_audit_sha256", "build_info_sha256", "files_checked", "elf_files") if key in result}
    return dict(schema_version=1, target=PLATFORM, source_sha=plan["source_sha"], inputs_stable=True,
                input_sha256=inputs, components=components, producer_receipt_sha256=file_hash(stage / "receipt.json"),
                qualification="native build/content/import smoke only")


def build(source, plan_path, plugin_dist, output, work, *, run=None):
    source, plugin_dist, output, work = validate_build_paths(source, plugin_dist, output, work)
    plan_path = Path(plan_path).resolve()
    if overlap(plan_path, output) or overlap(plan_path, work) or overlap(plan_path, source):
        raise ValueError("plan must be outside source, private work and output")
    plan = validate_plan(source, read_json(plan_path))
    approvals, proof = public_examples(source), plugin_inputs(source)
    plugin_sha = file_hash(plugin_dist)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    accepted = work / "accepted"
    accepted.mkdir()
    runner = run or Runner(work)
    private = work / "compiler"
    private.mkdir()
    (private / "home").mkdir()
    stage = work / "native-stage"
    stage.mkdir()
    # Its native child stays fresh; the bind mount is owned by the host user.
    write_json(work / "plugin-inputs.json", proof)
    write_json(work / "public-examples.json", approvals)
    write_json(accepted / "plan.json", plan)
    if not re.fullmatch(r"go version go1\.25(?:\.\d+)? linux/amd64\s*", runner(["go", "version"])):
        raise ValueError("candidate requires native linux/amd64 Go 1.25 host toolchain")
    runner(["docker", "pull", "--platform", PLATFORM, plan["python_image"]])
    base = json.loads(runner(["docker", "image", "inspect", "--format", "{{json .}}", plan["python_image"]]))
    if (base.get("Os"), base.get("Architecture")) != ("linux", "amd64") or not re.fullmatch(r"sha256:[0-9a-f]{64}", base.get("Id", "")):
        raise ValueError("pristine runtime image config/platform is invalid")
    runner(["docker", "image", "save", "--output", work / "base-image.tar", plan["python_image"]])
    build_id = f'{plan["producer"]["id"]}.{plan["producer"]["run_attempt"]}'
    builder_tag = "aiv-candidate-builder:" + build_id
    builder_context = work / "builder-context"
    builder_context.mkdir()
    shutil.copyfile(source / "deploy/native-packaging/Dockerfile.ci-builder", builder_context / "Dockerfile")
    runner(["docker", "build", "--platform", PLATFORM, "--build-arg", "PYTHON_IMAGE=" + plan["python_image"],
            "--tag", builder_tag, builder_context])
    # No secrets/host home or Docker socket is mounted into the compiler.
    runner(["docker", "run", "--rm", "--platform", PLATFORM, "--read-only", "--cpus=2", "--memory=6g", "--pids-limit=512",
            "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,exec,size=2g", "--env", "HOME=/work/home",
            "--env", "GIT_CONFIG_COUNT=1", "--env", "GIT_CONFIG_KEY_0=safe.directory", "--env", "GIT_CONFIG_VALUE_0=/repo",
            "--volume", str(source) + ":/repo:ro", "--volume", str(private) + ":/work",
            "--volume", str(stage) + ":/artifacts", builder_tag,
            "python", "/repo/deploy/native-packaging/build.py", "--source-root", "/repo", "--target", PLATFORM,
            "--component", "all", "--jobs", "1", "--work-dir", "/work/native", "--output-dir", "/artifacts/native"])
    # Docker mount /artifacts contains the producer's native output child.
    stage = stage / "native"
    write_json(accepted / "native-build-audit.json", native_audit(stage, source, plan))
    validate_plan(source, plan)
    if file_hash(plugin_dist) != plugin_sha:
        raise ValueError("plugin distribution changed during native build")
    probe = work / "aiv-http-probe"
    runner(["go", "build", "-trimpath", "-ldflags=-s -w", "-o", probe, source / "deploy/native-packaging/probe/main.go"],
           cwd=source, env=go_environment(work))
    build_clis(source, plan, work, accepted, runner)
    image_records = {}
    for component, identity in (("backend", "aivision-be"), ("agentlens", "agentlens")):
        info_path = work / f"{component}-build-info.json"
        write_json(info_path, make_component_info(source, identity, plan))
        context = work / (component + "-context")
        argv = [sys.executable, source / "deploy/native-packaging/images.py", "prepare", "--component", component,
                "--target", PLATFORM, "--source-root", source, "--source-revision", plan["source_sha"], "--stage-dir", stage,
                "--build-info", info_path, "--base-image", plan["python_image"], "--base-image-id", base["Id"],
                "--health-probe", probe, "--output-dir", context]
        if component == "backend":
            argv += ["--plugin-dist", plugin_dist, "--plugin-dist-sha256", plugin_sha, "--plugin-inputs", work / "plugin-inputs.json",
                     "--public-examples", work / "public-examples.json", "--installer-target", "linux-gnu-x64",
                     "--onboarding-stage", "linux-gnu-x64=" + str(stage)]
        runner(argv)
        context_receipt = read_json(context / "receipt.json")
        if (context_receipt.get("source_revision") != plan["source_sha"] or context_receipt.get("component") != component
                or context_receipt.get("target") != PLATFORM or context_receipt.get("base_image_id") != base["Id"]):
            raise ValueError("prepared context receipt identity differs")
        tag = f"aiv-candidate-{component}:{build_id}"
        runner(["docker", "build", "--platform", PLATFORM, "--tag", tag, context / "context"])
        image_path = work / f"{component}-image.tar"
        runner(["docker", "image", "save", "--output", image_path, tag])
        audit_path = work / f"{component}-image-audit.json"
        runner([sys.executable, source / "deploy/native-packaging/images.py", "audit-image", "--image-save", image_path,
                "--base-save", work / "base-image.tar", "--receipt", context / "receipt.json", "--output", audit_path])
        audit = read_json(audit_path)
        if audit.get("ok") is not True or audit.get("violations") or audit.get("target") != PLATFORM or not re.fullmatch(r"sha256:[0-9a-f]{64}", audit.get("image_config_sha256", "")):
            raise ValueError("saved candidate image did not pass the complete local content audit")
        shutil.copyfile(image_path, accepted / image_path.name)
        shutil.copyfile(audit_path, accepted / audit_path.name)
        write_json(accepted / f"{component}-context-receipt.json", context_receipt)
        image_records[component] = dict(local_tag=tag, local_config_id=audit["image_config_sha256"],
            archive_sha256=file_hash(image_path), base_config_id=base["Id"], registry_qualification=False)
    validate_plan(source, plan)
    if file_hash(plugin_dist) != plugin_sha or public_examples(source) != approvals or plugin_inputs(source) != proof:
        raise ValueError("provider inputs changed during candidate assembly")
    payloads = {path.name: dict(sha256=file_hash(path), size=path.stat().st_size) for path in sorted(accepted.iterdir())}
    write_json(accepted / "candidate-receipt.json", dict(schema_version=1, source_sha=plan["source_sha"],
        repository=plan["repository"], product_version=plan["product_version"], source_ci=plan["source_ci"], source_ci_gate=plan["source_ci_gate"], producer=plan["producer"],
        platform=PLATFORM, metadata_channel="rc", cli_profiles=CLI_PROFILES, python_image=plan["python_image"], images=image_records,
        plugin_dist_sha256=plugin_sha, artifacts=payloads, qualification=QUALIFICATION))
    validate_output(accepted)
    # A failure above leaves output empty, even if private staging is partial.
    for path in sorted(accepted.iterdir()):
        shutil.copyfile(path, output / path.name)
    validate_output(output)
    return read_json(output / "candidate-receipt.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--source-root", type=Path, required=True)
    plan.add_argument("--source-ci-json", type=Path, required=True)
    plan.add_argument("--producer-json", type=Path, required=True)
    plan.add_argument("--source-ci-jobs-json", type=Path, required=True)
    plan.add_argument("--python-image", required=True)
    plan.add_argument("--output", type=Path, required=True)
    produce = commands.add_parser("build")
    for argument in ("source-root", "plan", "plugin-dist", "output", "work"):
        produce.add_argument("--" + argument, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = make_plan(args.source_root, read_json(args.source_ci_json), read_json(args.producer_json), args.python_image, read_json(args.source_ci_jobs_json))
            if args.output.exists() or overlap(args.output.resolve(), args.source_root.resolve()):
                raise ValueError("plan output must be fresh and outside source")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, value)
        else:
            build(args.source_root, args.plan, args.plugin_dist, args.output, args.work)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError) as error:
        # Compiler stdout/stderr remains in private work, never the upload tree.
        parser.exit(1, "native candidate rejected: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
