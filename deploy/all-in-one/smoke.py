#!/usr/bin/env python3
"""Qualify a frozen All-in-One bundle on an owned native Linux runner."""

import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import uuid

from sample_trace import DemoError, Platform, prepare_demo, resource_id, wait_for_trace


MANIFESTS = {"amd64": "all-in-one-manifest.json", "arm64": "all-in-one-manifest-arm64.json"}
HOST_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
OWNER = "io.aivision.all-in-one.instance"
RELEASE = "io.aivision.all-in-one.release"
SHA256 = r"[0-9a-f]{64}"


class QualificationError(RuntimeError):
    """A safe failure label that may be printed into hosted CI logs."""


def require(condition, message):
    if not condition:
        raise QualificationError(message)


def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def digest_file(path):
    with path.open("rb") as stream:
        return digest_stream(stream)


def require_archive_parity(directory, manifest):
    expected = manifest["tar_sha256"]
    require(isinstance(expected, str) and re.fullmatch(SHA256, expected),
            "The candidate has no Docker tar identity.")
    with gzip.open(directory / manifest["archive"], "rb") as stream:
        require(digest_stream(stream) == expected, "The gzip archive contains different Docker tar bytes.")
    with subprocess.Popen(
        ["zstd", "-qdc", "--", str(directory / manifest["zstd_archive"])],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process:
        actual = digest_stream(process.stdout)
        require(process.wait() == 0 and actual == expected,
                "The zstd archive contains different Docker tar bytes.")



def file_at(directory, name):
    member = directory / name
    require(member.is_file() and not member.is_symlink(), f"Missing regular candidate file {name}.")
    return member


def read_candidate(directory, architecture, source_sha, build_id):
    manifest_path = file_at(directory, MANIFESTS[architecture])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest["source_candidate"]
    tag = manifest["release_tag"]
    require(
        type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
        and manifest["profile"] == "all-in-one"
        and manifest["platform"] == f"linux/{architecture}"
        and re.fullmatch(r"v\d+\.\d+\.\d+-daily\.\d{8}(?:\.\d+)?", tag)
        and manifest["base_release"] == tag
        and identity == {
            "source_repository": "antgroup/ai-vision", "source_sha": source_sha,
            "release_tag": tag, "build_id": build_id,
        }
        and re.fullmatch(r"sha256:" + SHA256, manifest["image_id"]),
        "The manifest does not describe this source, attempt and native architecture.",
    )

    image_name = f"aiv-all-in-one-linux-{architecture}.tar"
    cli_name = "aiv-linux-x64.tar.gz" if architecture == "amd64" else "aiv-linux-arm64.tar.gz"
    cli = manifest["agent_cli"]
    require(cli["archive"] == cli_name and cli["version"] == tag[1:],
            "The CLI in the personal edition manifest does not match its version or architecture.")

    entries = {}
    for line in file_at(directory, "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(rf"({SHA256})  ([A-Za-z0-9._-]+)", line)
        require(match and match[2] not in entries, "Invalid or duplicate candidate checksum.")
        entries[match[2]] = match[1]
    for name in (manifest_path.name, "release-manifest.json", "install-all-in-one.py", "sample-trace.py"):
        require(entries.get(name) == digest_file(file_at(directory, name)),
                f"Candidate checksum mismatch for {name}.")
    require(entries["sample-trace.py"] == digest_file(Path(__file__).with_name("sample_trace.py")),
            "The candidate sample helper differs from the source under test.")

    for name, expected_name, size, sha256 in (
        (manifest["archive"], image_name + ".gz", manifest["archive_bytes"], manifest["archive_sha256"]),
        (manifest["zstd_archive"], image_name + ".zst", manifest["zstd_archive_bytes"], manifest["zstd_archive_sha256"]),
        (cli["archive"], cli_name, cli["archive_bytes"], cli["archive_sha256"]),
    ):
        require(name == expected_name and type(size) is int and size > 0
                and re.fullmatch(SHA256, sha256) and entries.get(name) == sha256
                and file_at(directory, name).stat().st_size == size,
                f"Manifest and candidate inventory disagree for {expected_name}.")

    # The exact candidate installer rehashes both archives during the two actual loads.
    return manifest, entries


def command(args, *, name, timeout=60, cwd=None):
    result = subprocess.run(args, cwd=cwd, timeout=timeout, text=True, capture_output=True)
    if result.returncode:
        raise QualificationError(f"{name} exited with status {result.returncode}.")
    return result.stdout.strip()


def docker(*args, name, timeout=60):
    return command(["docker", *args], name=name, timeout=timeout)


def inspect(kind, resource, expression, *, missing_ok=False):
    if missing_ok:
        listing = ("--all", "--format", "{{.Names}}") if kind == "container" else ("--format", "{{.Name}}")
        names = docker(kind, "ls", *listing, name=f"List Docker {kind}s").splitlines()
        if resource not in names:
            return None
    value = docker(kind, "inspect", "--format", "{{json " + expression + "}}", resource,
                   name=f"Docker {kind} inspection")
    return json.loads(value)


def ensure_native(architecture):
    daemon = docker("info", "--format", "{{.OSType}}/{{.Architecture}}", name="Docker platform check")
    system, _, machine = daemon.partition("/")
    require(platform.system() == "Linux" and HOST_ARCH.get(platform.machine()) == architecture
            and system == "linux" and HOST_ARCH.get(machine) == architecture,
            "The Linux execution host and Docker daemon must both be the selected native architecture.")


def two_free_ports():
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket(socket.AF_INET)) for _ in range(2)]
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [listener.getsockname()[1] for listener in sockets]


def verify_cli_installation(directory, version):
    with TemporaryDirectory(prefix="aiv-candidate-cli-") as install_dir:
        command([
            sys.executable, str(directory / "install-all-in-one.py"), "--from-dir", str(directory),
            "--install-cli", "--install-dir", install_dir,
        ], name="Install the matching candidate CLI")
        require(command([str(Path(install_dir) / "aiv"), "--version"], name="Run the packaged CLI") == f"aiv {version}",
                "The installed CLI did not report the expected product version.")


def run_installer(directory, name, web, otlp, archive_format):
    print(f"Installing candidate via {archive_format} on the native Docker host.", flush=True)
    args = [
        str(directory / "install-all-in-one.py"), "--from-dir", str(directory), "--name", name,
        "--web-port", str(web), "--otlp-port", str(otlp), "--archive-format", archive_format,
        "--timeout", "420",
    ]
    try:
        command([sys.executable, *args], name="Candidate installer", timeout=900)
    except QualificationError:
        state = inspect("container", name, ".State", missing_ok=True)
        if state is not None:
            print("Candidate container evidence:", json.dumps({
                "running": state.get("Running") is True,
                "exit_code": state.get("ExitCode"),
                "health": state.get("Health", {}).get("Status"),
            }), flush=True)
        raise


def verify_running(name, manifest, web, otlp):
    # Compare the running container's content identity, platform and attached named-volume identity.
    state = inspect("container", name, ".State")
    require(state.get("Running") is True and state.get("Health", {}).get("Status") == "healthy",
            "The installed container is not healthy.")
    image_id = inspect("container", name, ".Image")
    require(image_id == manifest["image_id"], "The installed container uses different image bytes.")
    architecture = docker("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id,
                          name="Docker image platform check")
    require(architecture == manifest["platform"], "The installed image architecture differs.")
    mounts = inspect("container", name, ".Mounts")
    require(any(
        mount["Type"] == "volume" and mount["Name"] == name + "-data" and mount["Destination"] == "/data"
        for mount in mounts
    ), "The candidate has no owned named data volume.")
    labels = inspect("volume", name + "-data", ".Labels")
    require(labels.get(OWNER) == name and labels.get(RELEASE) == manifest["release_tag"],
            "The candidate volume belongs to a different run or release.")
    ports = inspect("container", name, ".NetworkSettings.Ports")
    require({key: bindings for key, bindings in ports.items() if bindings} == {
        "5174/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(web)}],
        "4318/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(otlp)}],
    }, "The candidate must expose only its two requested loopback ports.")
    return inspect("container", name, ".Id")


def start_diagnosis(client, space_id, demo):
    scope = f"/spaces/{space_id}/diagnose"
    body = {
        "request_id": str(uuid.uuid4()), "trace_ids": [demo["trace_id"]],
        "diagnoser_uuid": demo["diagnoser_uuid"],
    }
    params = {"agent_uuid": demo["agent_uuid"]}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            value = client.api(scope + "/runs", body=body, params=params)
        except DemoError as error:
            if error.status != 503:
                raise
            time.sleep(2)
            continue
        require(value.get("request_id") == body["request_id"], "Diagnosis request identity changed.")
        if value.get("state") == "accepted":
            return {**value, **{key: resource_id(value[key]) for key in ("job_uuid", "task_uuid")}}
        require(value.get("state") == "provisioning" and value.get("retry_after_seconds") == 2,
                "Unexpected diagnosis provisioning response.")
        time.sleep(2)
    raise QualificationError("The sample rule diagnosis never finished provisioning.")


def read_diagnosis(client, space_id, demo, receipt):
    base = f"/spaces/{space_id}/diagnose"
    task_id, job_id = receipt["task_uuid"], receipt["job_uuid"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        task = client.api(f"{base}/tasks/{task_id}")
        require(task.get("uuid") == task_id and task.get("job_uuid") == job_id,
                "Diagnosis returned a different persisted task.")
        require(task.get("status") not in {"failed", "canceled"},
                "The persisted sample diagnosis did not succeed.")
        if task.get("status") == "succeeded":
            listing = client.api(base + "/records", params={"task_uuid": task_id, "job_uuid": job_id})
            records = listing.get("items", [])
            if listing.get("total") == 1 and len(records) == 1:
                record_id = resource_id(records[0]["uuid"])
                record = client.api(f"{base}/records/{record_id}")
                break
        time.sleep(2)
    else:
        raise QualificationError("The sample diagnosis did not yield a persisted rule result.")

    require(record.get("task_uuid") == task_id and record.get("job_uuid") == job_id
            and record.get("uuid") == record_id and record.get("target_id") == demo["trace_id"]
            and record.get("diagnoser_uuid") == demo["diagnoser_uuid"]
            and record.get("verdict") == "bad"
            and record.get("execution", {}).get("status") == "succeeded",
            "The diagnosis did not evaluate the expected failing sample Trace.")
    metric = next((item for item in record["metrics"] if item.get("name") == "tool_success_rate"), {})
    failures = metric.get("evidence", {}).get("counts", {}).get("failure")
    require(type(failures) is int and failures > 0 and metric.get("verdict") == "bad",
            "The persisted Rule did not report the sample failed tool.")
    return record_id


def stop_container(name):
    docker("stop", "--time", "120", name, name="Graceful candidate shutdown", timeout=135)
    state = inspect("container", name, ".State")
    require(state.get("Running") is False and state.get("ExitCode") == 0
            and state.get("OOMKilled") is False, "The candidate did not stop normally.")


def cleanup(name):
    errors = []
    for kind, resource in (("container", name), ("volume", name + "-data"), ("network", name + "-network")):
        try:
            expression = ".Config.Labels" if kind == "container" else ".Labels"
            labels = inspect(kind, resource, expression, missing_ok=True)
            if labels is None:
                continue
            require(labels.get(OWNER) == name, "Refusing to remove unowned smoke resources.")
            remove = ["rm", "--force", resource] if kind == "container" else ["rm", resource]
            docker(kind, *remove, name=f"Remove owned Docker {kind}")
        except (OSError, QualificationError, subprocess.TimeoutExpired):
            errors.append(kind)
    require(not errors, "Could not remove all owned Docker resources.")


def qualify(directory, name, manifest, entries):
    web, otlp = two_free_ports()
    for kind, resource in (("container", name), ("volume", name + "-data"), ("network", name + "-network")):
        require(inspect(kind, resource, ".", missing_ok=True) is None, "Smoke resource already exists.")

    try:
        require_archive_parity(directory, manifest)
        verify_cli_installation(directory, manifest["agent_cli"]["version"])
        run_installer(directory, name, web, otlp, "zstd")
        first_container = verify_running(name, manifest, web, otlp)
        email, password = name + "@example.invalid", secrets.token_urlsafe(32) + "!9"
        client = Platform(web)
        client.api("/auth/csrf")
        signup = client.api("/auth/register", body={
            "email": email, "name": "All-in-One candidate smoke",
            "password": password, "password_confirm": password,
        })
        space_id = resource_id(signup["personal_space"]["space_id"])
        require(signup["personal_space"]["type"] == 1, "Signup did not create a personal space.")
        user_id = resource_id(signup["user"]["user_uuid"])
        require(client.login(email, password).get("user_uuid") == user_id,
                "Could not authenticate the newly registered account.")

        print("Importing the product Quickstart sample and verifying its trace and rule result.", flush=True)
        demo = prepare_demo(client, space_id)
        wait_for_trace(client, space_id, demo)
        wait_for_trace(client, space_id, demo, comparison=True)
        receipt = start_diagnosis(client, space_id, demo)
        record_id = read_diagnosis(client, space_id, demo, receipt)

        stop_container(name)
        docker("container", "rm", name, name="Remove stopped candidate container")
        run_installer(directory, name, web, otlp, "gzip")
        second_container = verify_running(name, manifest, web, otlp)
        require(second_container != first_container, "The installer did not recreate the container.")
        restored = Platform(web)
        require(restored.login(email, password).get("user_uuid") == user_id,
                "The account did not survive named-volume recreation.")
        wait_for_trace(restored, space_id, demo)
        require(read_diagnosis(restored, space_id, demo, receipt) == record_id,
                "The persisted diagnosis record did not survive named-volume recreation.")
        stop_container(name)
    finally:
        cleanup(name)

    return {
        "schema_version": 1, "status": "passed", "platform": manifest["platform"],
        "source_sha": manifest["source_candidate"]["source_sha"],
        "build_id": manifest["source_candidate"]["build_id"],
        "release_tag": manifest["release_tag"], "manifest_sha256": entries[MANIFESTS[manifest["platform"].split("/")[-1]]],
        "archive": manifest["archive"], "archive_sha256": manifest["archive_sha256"],
        "zstd_archive_sha256": manifest["zstd_archive_sha256"], "tar_sha256": manifest["tar_sha256"],
        "image_id": manifest["image_id"],
        "checks": dict.fromkeys(("startup", "signup", "trace", "diagnosis", "shutdown", "recreation", "cleanup"), True),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--architecture", choices=MANIFESTS, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--build-id", default=f"{os.environ.get('GITHUB_RUN_ID', '')}.{os.environ.get('GITHUB_RUN_ATTEMPT', '')}")
    args = parser.parse_args(argv)

    try:
        require(re.fullmatch(r"[1-9][0-9]*\.[1-9][0-9]*", args.build_id),
                "Provide the candidate's exact run and attempt with --build-id.")
        require(not args.receipt.exists() and not args.receipt.is_symlink(), "Use a new qualification receipt path.")
        source_sha = command(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                             name="Read checked-out candidate source")
        directory = args.directory.resolve()
        manifest, entries = read_candidate(directory, args.architecture, source_sha, args.build_id)
        ensure_native(args.architecture)
        result = qualify(directory, "aiv-aio-smoke-" + secrets.token_hex(8), manifest, entries)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (DemoError, QualificationError) as error:
        parser.exit(1, f"All-in-One smoke: {error}\n")
    except (AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"All-in-One smoke: {type(error).__name__}.\n")
    print("All-in-One candidate passed signup, sample, diagnosis, shutdown and volume recreation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
